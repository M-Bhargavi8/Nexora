"""Provider-independent access to language models.

`app.ai.base` defines the interface, `app.ai.providers` holds the adapters,
and `app.ai.gateway.get_provider()` picks the configured one. Import from
`gateway`; never import an adapter directly outside this package.
"""
