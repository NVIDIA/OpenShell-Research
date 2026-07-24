# RegexEngine policy example

This example keeps entity behavior in the OpenShell policy configuration.
`privacy-guard-config.yaml` contains the complete structured
`RegexPatternCatalog` accepted by Privacy Guard.

Privacy Guard does not ship authoritative regex presets. Copy and adapt this
example and the larger `patterns.yaml` reference catalog for the data you
actually need to identify, and test it against representative worst-case inputs
before deployment.

The current OpenShell policy flow does not expand catalog file paths. A catalog
must therefore be inline before it is passed to Privacy Guard. Transparent file
expansion and larger prepared catalogs require coordinated upstream OpenShell
support; this project does not fork the copied `.proto`.
