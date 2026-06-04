import reflex as rx

# Allow running `reflex run` directly inside /oaepp.
config = rx.Config(
    app_name="oaepp",
    app_module_import="app",
    frontend_port=3000,
    backend_port=8000,
)
