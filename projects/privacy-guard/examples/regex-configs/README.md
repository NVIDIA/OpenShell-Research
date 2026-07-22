# Regex scanner configurations

`customer.yaml` and `hipaa.yaml` demonstrate the single-profile entity-list
shape. `profiles.yaml` combines both catalogs under explicit profile names. Its
profile is a RegexScanner option, so select one after the scanner-option
separator, for example:

```bash
privacy-guard --scanner-config profiles.yaml -- --profile customer-support
privacy-guard --scanner-config profiles.yaml -- --profile hipaa
```

The HIPAA catalog is only a starting rule set. It is not a claim of compliance.
Deployers remain responsible for validating detection behavior and implementing
the operational, administrative, and technical controls their environment
requires.
