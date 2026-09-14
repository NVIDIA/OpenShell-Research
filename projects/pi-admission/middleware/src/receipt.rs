use std::time::{SystemTime, UNIX_EPOCH};

use base64::{Engine, engine::general_purpose};
use ed25519_dalek::{Signer, SigningKey, Verifier, VerifyingKey};
use rand::{RngCore, rngs::OsRng};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use crate::policy::{POLICY_ID, Projection, ProviderTarget};

const LIFETIME_SECONDS: u64 = 300;
const CLOCK_SKEW_SECONDS: u64 = 5;
const MAX_RECEIPT_BYTES: usize = 8 * 1024;

#[derive(Clone)]
pub struct ReceiptAuthority {
    signing_key: SigningKey,
    verifying_key: VerifyingKey,
    key_id: String,
}

pub(crate) struct ReceiptContext<'a> {
    pub middleware_name: &'a str,
    pub sandbox_id: &'a str,
    pub target: &'a ProviderTarget,
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Claims {
    receipt_version: String,
    canonicalization_version: String,
    harness: String,
    harness_version: String,
    harness_schema: String,
    hook: String,
    middleware_binding: String,
    policy_identity: String,
    sandbox_id: String,
    session_id: String,
    submission_id: String,
    receipt_id: String,
    provider_adapter_schema: String,
    host: String,
    port: u32,
    subject_kind: String,
    subject_hash: String,
    entry_count: usize,
    issued_at: u64,
    expires_at: u64,
    key_id: String,
}

impl ReceiptAuthority {
    pub fn new(signing_key: SigningKey) -> Self {
        let verifying_key = signing_key.verifying_key();
        let key_id = hex(&Sha256::digest(verifying_key.as_bytes()))[..16].to_owned();
        Self {
            signing_key,
            verifying_key,
            key_id,
        }
    }

    pub(crate) fn issue_header(
        &self,
        projection: &Projection,
        context: ReceiptContext<'_>,
        session_id: &str,
        submission_id: &str,
    ) -> Result<String, &'static str> {
        self.issue_header_at(projection, context, session_id, submission_id, now()?)
    }

    fn issue_header_at(
        &self,
        projection: &Projection,
        context: ReceiptContext<'_>,
        session_id: &str,
        submission_id: &str,
        issued_at: u64,
    ) -> Result<String, &'static str> {
        let mut identifier = [0_u8; 16];
        OsRng.fill_bytes(&mut identifier);
        let claims = Claims {
            receipt_version: "pi-admission-receipt.v1".to_owned(),
            canonicalization_version: "canonical-json.v1".to_owned(),
            harness: "pi".to_owned(),
            harness_version: "sdk-v1".to_owned(),
            harness_schema: "openshell.pi-provider-context.v1".to_owned(),
            hook: "provider_context".to_owned(),
            middleware_binding: context.middleware_name.to_owned(),
            policy_identity: POLICY_ID.to_owned(),
            sandbox_id: context.sandbox_id.to_owned(),
            session_id: session_id.to_owned(),
            submission_id: submission_id.to_owned(),
            receipt_id: hex(&identifier),
            provider_adapter_schema: "openai.request.v1".to_owned(),
            host: context.target.host.clone(),
            port: context.target.port,
            subject_kind: "context".to_owned(),
            subject_hash: subject(projection)?,
            entry_count: projection.len(),
            issued_at,
            expires_at: issued_at + LIFETIME_SECONDS,
            key_id: self.key_id.clone(),
        };
        let payload = serde_json::to_vec(&claims).map_err(|_| "receipt_issuance_failed")?;
        let signature = self.signing_key.sign(&payload);
        let token = format!(
            "pr1.{}.{}",
            general_purpose::URL_SAFE_NO_PAD.encode(payload),
            general_purpose::URL_SAFE_NO_PAD.encode(signature.to_bytes())
        );
        Ok(general_purpose::URL_SAFE.encode(token.as_bytes()))
    }

    pub(crate) fn verify_header(
        &self,
        header: &str,
        projection: &Projection,
        context: ReceiptContext<'_>,
    ) -> Result<(), &'static str> {
        self.verify_header_at(header, projection, context, now()?)
    }

    fn verify_header_at(
        &self,
        header: &str,
        projection: &Projection,
        context: ReceiptContext<'_>,
        current: u64,
    ) -> Result<(), &'static str> {
        if header.len() > MAX_RECEIPT_BYTES * 4 / 3 + 4 {
            return Err("receipt_malformed");
        }
        let token = general_purpose::URL_SAFE
            .decode(header)
            .map_err(|_| "receipt_malformed")?;
        if token.len() > MAX_RECEIPT_BYTES {
            return Err("receipt_malformed");
        }
        let token = std::str::from_utf8(&token).map_err(|_| "receipt_malformed")?;
        let mut parts = token.split('.');
        if parts.next() != Some("pr1") {
            return Err("receipt_malformed");
        }
        let payload = general_purpose::URL_SAFE_NO_PAD
            .decode(parts.next().ok_or("receipt_malformed")?)
            .map_err(|_| "receipt_malformed")?;
        let signature = general_purpose::URL_SAFE_NO_PAD
            .decode(parts.next().ok_or("receipt_malformed")?)
            .map_err(|_| "receipt_malformed")?;
        if parts.next().is_some() {
            return Err("receipt_malformed");
        }
        let signature =
            ed25519_dalek::Signature::from_slice(&signature).map_err(|_| "receipt_malformed")?;
        self.verifying_key
            .verify(&payload, &signature)
            .map_err(|_| "receipt_signature_invalid")?;
        let claims: Claims = serde_json::from_slice(&payload).map_err(|_| "receipt_malformed")?;
        if serde_json::to_vec(&claims).map_err(|_| "receipt_malformed")? != payload {
            return Err("receipt_malformed");
        }
        if claims.issued_at > current + CLOCK_SKEW_SECONDS {
            return Err("receipt_not_yet_valid");
        }
        if claims.expires_at <= current || claims.expires_at <= claims.issued_at {
            return Err("receipt_expired");
        }
        if claims.receipt_version != "pi-admission-receipt.v1"
            || claims.canonicalization_version != "canonical-json.v1"
            || claims.harness != "pi"
            || claims.harness_version != "sdk-v1"
            || claims.harness_schema != "openshell.pi-provider-context.v1"
            || claims.hook != "provider_context"
            || claims.middleware_binding != context.middleware_name
            || claims.policy_identity != POLICY_ID
            || claims.sandbox_id != context.sandbox_id
            || claims.provider_adapter_schema != "openai.request.v1"
            || claims.host != context.target.host
            || claims.port != context.target.port
            || claims.subject_kind != "context"
            || claims.key_id != self.key_id
        {
            return Err("receipt_context_mismatch");
        }
        if claims.entry_count != projection.len() || claims.subject_hash != subject(projection)? {
            return Err("receipt_content_mismatch");
        }
        Ok(())
    }
}

fn subject(projection: &Projection) -> Result<String, &'static str> {
    let bytes = serde_json::to_vec(projection).map_err(|_| "receipt_content_invalid")?;
    Ok(hex(&Sha256::digest(bytes)))
}

fn now() -> Result<u64, &'static str> {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs())
        .map_err(|_| "clock_invalid")
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::policy::ContextEntry;

    fn target() -> ProviderTarget {
        ProviderTarget {
            scheme: "https".to_owned(),
            host: "api.example.test".to_owned(),
            port: 443,
            method: "POST".to_owned(),
            path: "/v1/chat/completions".to_owned(),
            query: String::new(),
        }
    }

    #[test]
    fn receipt_binds_content_destination_and_sandbox() {
        let authority = ReceiptAuthority::new(SigningKey::from_bytes(&[7; 32]));
        let target = target();
        let projection = vec![ContextEntry::User {
            text: "approved".to_owned(),
        }];
        let context = ReceiptContext {
            middleware_name: "pi-admission",
            sandbox_id: "sandbox-1",
            target: &target,
        };
        let receipt = authority
            .issue_header(&projection, context, "session", "submission")
            .unwrap();
        let context = ReceiptContext {
            middleware_name: "pi-admission",
            sandbox_id: "sandbox-1",
            target: &target,
        };
        assert!(
            authority
                .verify_header(&receipt, &projection, context)
                .is_ok()
        );
        let mut invalid = general_purpose::URL_SAFE.decode(&receipt).unwrap();
        let start = invalid.iter().rposition(|byte| *byte == b'.').unwrap() + 1;
        invalid[start] = if invalid[start] == b'A' { b'B' } else { b'A' };
        let invalid = general_purpose::URL_SAFE.encode(invalid);
        let context = ReceiptContext {
            middleware_name: "pi-admission",
            sandbox_id: "sandbox-1",
            target: &target,
        };
        assert_eq!(
            authority.verify_header(&invalid, &projection, context),
            Err("receipt_signature_invalid")
        );
        let changed = vec![ContextEntry::User {
            text: "changed".to_owned(),
        }];
        let context = ReceiptContext {
            middleware_name: "pi-admission",
            sandbox_id: "sandbox-1",
            target: &target,
        };
        assert_eq!(
            authority.verify_header(&receipt, &changed, context),
            Err("receipt_content_mismatch")
        );

        let wrong_target = ProviderTarget {
            host: "other.example.test".to_owned(),
            ..target.clone()
        };
        let context = ReceiptContext {
            middleware_name: "pi-admission",
            sandbox_id: "sandbox-1",
            target: &wrong_target,
        };
        assert_eq!(
            authority.verify_header(&receipt, &projection, context),
            Err("receipt_context_mismatch")
        );
        let context = ReceiptContext {
            middleware_name: "pi-admission",
            sandbox_id: "sandbox-2",
            target: &target,
        };
        assert_eq!(
            authority.verify_header(&receipt, &projection, context),
            Err("receipt_context_mismatch")
        );
    }

    #[test]
    fn expired_receipt_fails_closed() {
        let authority = ReceiptAuthority::new(SigningKey::from_bytes(&[9; 32]));
        let target = target();
        let projection = vec![ContextEntry::User {
            text: "approved".to_owned(),
        }];
        let context = ReceiptContext {
            middleware_name: "pi-admission",
            sandbox_id: "sandbox-1",
            target: &target,
        };
        let receipt = authority
            .issue_header_at(&projection, context, "session", "submission", 100)
            .unwrap();
        let context = ReceiptContext {
            middleware_name: "pi-admission",
            sandbox_id: "sandbox-1",
            target: &target,
        };
        assert_eq!(
            authority.verify_header_at(&receipt, &projection, context, 400),
            Err("receipt_expired")
        );
    }
}
