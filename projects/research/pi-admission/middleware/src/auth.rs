use std::{error::Error, fs, path::Path};

use jsonwebtoken::{Algorithm, DecodingKey, Validation, decode, decode_header};
use serde::Deserialize;
use tonic::{Status, metadata::MetadataMap};

#[derive(Clone)]
pub(crate) struct GatewayAuthentication {
    key: DecodingKey,
    validation: Validation,
}

#[derive(Deserialize)]
struct Claims {
    caller_kind: String,
    sandbox_id: Option<String>,
}

impl GatewayAuthentication {
    pub(crate) fn from_pem(
        path: &Path,
        issuer: &str,
        audience: &str,
    ) -> Result<Self, Box<dyn Error>> {
        let key = DecodingKey::from_ed_pem(&fs::read(path)?)?;
        let mut validation = Validation::new(Algorithm::EdDSA);
        validation.set_issuer(&[issuer]);
        validation.set_audience(&[audience]);
        validation.set_required_spec_claims(&["iss", "aud", "exp", "iat"]);
        Ok(Self { key, validation })
    }

    pub(crate) fn verify(
        &self,
        metadata: &MetadataMap,
        expected_kind: &str,
        sandbox_id: Option<&str>,
    ) -> Result<(), Status> {
        let values: Vec<_> = metadata.get_all("authorization").iter().collect();
        if values.len() != 1 {
            return Err(Status::unauthenticated("authentication required"));
        }
        let value = values[0]
            .to_str()
            .map_err(|_| Status::unauthenticated("authentication required"))?;
        let token = value
            .strip_prefix("Bearer ")
            .filter(|token| !token.is_empty())
            .ok_or_else(|| Status::unauthenticated("authentication required"))?;
        let header =
            decode_header(token).map_err(|_| Status::unauthenticated("authentication failed"))?;
        if header.typ.as_deref() != Some("openshell-ext+jwt") {
            return Err(Status::unauthenticated("incorrect token type"));
        }
        let claims = decode::<Claims>(token, &self.key, &self.validation)
            .map_err(|_| Status::unauthenticated("authentication failed"))?
            .claims;
        if claims.caller_kind != expected_kind
            || sandbox_id.is_some_and(|expected| claims.sandbox_id.as_deref() != Some(expected))
        {
            return Err(Status::permission_denied("caller context mismatch"));
        }
        Ok(())
    }
}
