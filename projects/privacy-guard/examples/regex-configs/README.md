# Regex scanner configurations

`customer.yaml` and `hipaa.yaml` demonstrate the single-profile entity-list
shape. `profiles.yaml` combines both catalogs under explicit profile names; run
Privacy Guard with `--profile customer-support` or `--profile hipaa` when using
that file.

The HIPAA catalog is only a starting rule set. It is not a claim of compliance.
Deployers remain responsible for validating detection behavior and implementing
the operational, administrative, and technical controls their environment
requires.
