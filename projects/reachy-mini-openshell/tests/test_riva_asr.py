from reachy_mini_conversation_app.riva_asr import RivaAsrConfig


def test_riva_config_accepts_http_endpoint_url(monkeypatch):
    """Riva endpoint URLs should be normalized to the host:port form expected by the client."""
    monkeypatch.setenv("RIVA_SERVER_URI", "http://192.168.1.57:9000")
    monkeypatch.delenv("RIVA_USE_SSL", raising=False)

    config = RivaAsrConfig.from_env()

    assert config.server_uri == "192.168.1.57:9000"
    assert config.use_ssl is False


def test_riva_config_infers_ssl_from_https_endpoint_url(monkeypatch):
    """HTTPS Riva endpoint URLs should enable TLS unless explicitly overridden."""
    monkeypatch.setenv("RIVA_SERVER_URI", "https://riva.example.test:443")
    monkeypatch.delenv("RIVA_USE_SSL", raising=False)

    config = RivaAsrConfig.from_env()

    assert config.server_uri == "riva.example.test:443"
    assert config.use_ssl is True


def test_riva_use_ssl_env_overrides_endpoint_url_scheme(monkeypatch):
    """RIVA_USE_SSL should keep explicit operator configuration authoritative."""
    monkeypatch.setenv("RIVA_SERVER_URI", "https://riva.example.test:443")
    monkeypatch.setenv("RIVA_USE_SSL", "false")

    config = RivaAsrConfig.from_env()

    assert config.server_uri == "riva.example.test:443"
    assert config.use_ssl is False
