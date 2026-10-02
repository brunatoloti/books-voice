from __future__ import annotations

from collections.abc import Mapping

import streamlit as st
import streamlit_authenticator as stauth


def _plain(value):
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    return value


def _resolve_username(credentials: dict, login_id: str) -> str | None:
    normalized = login_id.strip().lower()
    if not normalized:
        return None

    usernames = credentials.get("usernames", {})
    for username, details in usernames.items():
        if str(username).strip().lower() == normalized:
            return str(username).strip().lower()
        if str(details.get("email", "")).strip().lower() == normalized:
            return str(username).strip().lower()
    return None


def _validate_config(auth_config: dict) -> str | None:
    cookie = auth_config.get("cookie", {})
    credentials = auth_config.get("credentials", {})
    usernames = credentials.get("usernames", {})

    if not usernames:
        return "Nenhum usuário foi configurado em auth.credentials.usernames."
    if "SUBSTITUA" in str(cookie.get("key", "")).upper():
        return "A chave de cookie ainda contém o valor de exemplo."
    for username, details in usernames.items():
        password = str(details.get("password", ""))
        if not password or "SUBSTITUA" in password.upper():
            return f"A senha do usuário '{username}' ainda não foi configurada."
    return None


def require_login():
    try:
        auth_config = _plain(st.secrets["auth"])
        cookie = auth_config["cookie"]
        credentials = auth_config["credentials"]
    except (KeyError, TypeError):
        st.error(
            "A autenticação ainda não foi configurada. Adicione as seções "
            "[auth.cookie] e [auth.credentials.usernames] aos Secrets do Streamlit."
        )
        st.stop()

    config_error = _validate_config(auth_config)
    if config_error:
        st.error(f"Configuração de login incompleta: {config_error}")
        st.stop()

    authenticator = stauth.Authenticate(
        credentials=credentials,
        cookie_name=str(cookie["name"]),
        cookie_key=str(cookie["key"]),
        cookie_expiry_days=float(cookie.get("expiry_days", 30)),
        auto_hash=True,
    )
    authenticator.login(location="unrendered")

    status = st.session_state.get("authentication_status")
    if status is not True:
        with st.form("epub_voz_login", clear_on_submit=False):
            st.subheader("Entrar")
            login_id = st.text_input("Usuário ou e-mail", autocomplete="username")
            password = st.text_input(
                "Senha", type="password", autocomplete="current-password"
            )
            submitted = st.form_submit_button("Entrar", use_container_width=True)

        if submitted:
            username = _resolve_username(credentials, login_id)
            authenticated = bool(
                username
                and authenticator.authentication_controller.login(username, password)
            )
            if authenticated:
                authenticator.cookie_controller.set_cookie()
                st.rerun()
            st.session_state["authentication_status"] = False

        if st.session_state.get("authentication_status") is False:
            st.error("Usuário, e-mail ou senha incorretos.")
        else:
            st.info("Entre para acessar seus audiolivros e seu dashboard.")
        st.stop()

    username = str(st.session_state.get("username") or "")
    display_name = str(st.session_state.get("name") or username)
    return authenticator, username, display_name
