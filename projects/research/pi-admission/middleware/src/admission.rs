use std::{fs, path::PathBuf, sync::Arc};

use axum::{
    Json, Router,
    body::Bytes,
    extract::{DefaultBodyLimit, State},
    http::{HeaderMap, StatusCode, header},
    response::{IntoResponse, Response},
    routing::post,
};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use subtle::ConstantTimeEq;

use crate::{
    MAX_BODY_BYTES,
    auth::GatewayAuthentication,
    policy::{CandidateDecision, POLICY_ID, Projection, ProviderTarget, evaluate_candidate},
    receipt::{ReceiptAuthority, ReceiptContext},
};

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AdmissionConfig {
    pub listen: String,
    pub tls_certificate: PathBuf,
    pub tls_private_key: PathBuf,
    pub gateway_public_key: PathBuf,
    pub gateway_issuer: String,
    pub gateway_audience: String,
    pub middleware_name: String,
    pub bearer_token: String,
    pub sandbox_id_file: PathBuf,
    pub provider_target: ProviderTarget,
}

impl AdmissionConfig {
    pub(crate) fn authentication(
        &self,
    ) -> Result<GatewayAuthentication, Box<dyn std::error::Error>> {
        GatewayAuthentication::from_pem(
            &self.gateway_public_key,
            &self.gateway_issuer,
            &self.gateway_audience,
        )
    }
}

#[derive(Clone)]
struct AppState {
    config: Arc<AdmissionConfig>,
    receipts: Arc<ReceiptAuthority>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct AdmissionCall {
    kind: String,
    session_id: String,
    submission_id: String,
    body: Value,
}

#[derive(Serialize)]
struct AdmissionResponse {
    decision: &'static str,
    reason_code: Option<&'static str>,
    replacement: Option<Value>,
    receipt: Option<String>,
    policy_identity: &'static str,
}

pub fn admission_router(config: Arc<AdmissionConfig>, receipts: Arc<ReceiptAuthority>) -> Router {
    Router::new()
        .route("/v1/admission", post(admit))
        .layer(DefaultBodyLimit::max(MAX_BODY_BYTES as usize))
        .with_state(AppState { config, receipts })
}

async fn admit(State(state): State<AppState>, headers: HeaderMap, body: Bytes) -> Response {
    if !authorized(&headers, &state.config.bearer_token) {
        return (StatusCode::UNAUTHORIZED, "admission authentication failed").into_response();
    }
    let call: AdmissionCall = match serde_json::from_slice(&body) {
        Ok(call) => call,
        Err(_) => return (StatusCode::BAD_REQUEST, "invalid admission request").into_response(),
    };
    if !bounded_identifier(&call.session_id) || !bounded_identifier(&call.submission_id) {
        return (StatusCode::BAD_REQUEST, "invalid admission request").into_response();
    }
    let sandbox_id = match fs::read_to_string(&state.config.sandbox_id_file) {
        Ok(value) if bounded_identifier(value.trim()) => value.trim().to_owned(),
        _ => {
            return (
                StatusCode::SERVICE_UNAVAILABLE,
                "admission is not provisioned",
            )
                .into_response();
        }
    };
    let response = match evaluate_candidate(&call.kind, call.body.clone()) {
        CandidateDecision::Deny(code) => AdmissionResponse {
            decision: "deny",
            reason_code: Some(code),
            replacement: None,
            receipt: None,
            policy_identity: POLICY_ID,
        },
        CandidateDecision::Replace(replacement) => AdmissionResponse {
            decision: "replace",
            reason_code: None,
            replacement: Some(replacement),
            receipt: None,
            policy_identity: POLICY_ID,
        },
        CandidateDecision::Allow => {
            let receipt = if call.kind == "provider_context" {
                let projection: Projection = match serde_json::from_value(
                    call.body.get("entries").cloned().unwrap_or(Value::Null),
                ) {
                    Ok(projection) => projection,
                    Err(_) => {
                        return (StatusCode::BAD_REQUEST, "invalid admission request")
                            .into_response();
                    }
                };
                let context = ReceiptContext {
                    middleware_name: &state.config.middleware_name,
                    sandbox_id: &sandbox_id,
                    target: &state.config.provider_target,
                };
                match state.receipts.issue_header(
                    &projection,
                    context,
                    &call.session_id,
                    &call.submission_id,
                ) {
                    Ok(receipt) => Some(receipt),
                    Err(_) => {
                        return (StatusCode::SERVICE_UNAVAILABLE, "admission is unavailable")
                            .into_response();
                    }
                }
            } else {
                None
            };
            AdmissionResponse {
                decision: "allow",
                reason_code: None,
                replacement: None,
                receipt,
                policy_identity: POLICY_ID,
            }
        }
    };
    Json(response).into_response()
}

fn authorized(headers: &HeaderMap, token: &str) -> bool {
    let values: Vec<_> = headers.get_all(header::AUTHORIZATION).iter().collect();
    if values.len() != 1 {
        return false;
    }
    let expected = format!("Bearer {token}");
    values[0].as_bytes().ct_eq(expected.as_bytes()).into()
}

fn bounded_identifier(value: &str) -> bool {
    !value.is_empty() && value.len() <= 1024 && !value.chars().any(char::is_control)
}

#[cfg(test)]
mod tests {
    use super::*;
    use ed25519_dalek::SigningKey;
    use tokio::{
        io::{AsyncReadExt, AsyncWriteExt},
        net::{TcpListener, TcpStream},
    };

    #[tokio::test]
    async fn real_http_transport_allows_replaces_denies_and_authenticates() {
        let sandbox_id_file =
            std::env::temp_dir().join(format!("pi-admission-sandbox-{}", std::process::id()));
        fs::write(&sandbox_id_file, "sandbox-1\n").unwrap();
        let config = Arc::new(AdmissionConfig {
            listen: "127.0.0.1:0".to_owned(),
            tls_certificate: PathBuf::new(),
            tls_private_key: PathBuf::new(),
            gateway_public_key: PathBuf::new(),
            gateway_issuer: "issuer".to_owned(),
            gateway_audience: "audience".to_owned(),
            middleware_name: "pi-admission".to_owned(),
            bearer_token: "secret".to_owned(),
            sandbox_id_file: sandbox_id_file.clone(),
            provider_target: ProviderTarget {
                scheme: "https".to_owned(),
                host: "api.example.test".to_owned(),
                port: 443,
                method: "POST".to_owned(),
                path: "/v1/chat/completions".to_owned(),
                query: String::new(),
            },
        });
        let receipts = Arc::new(ReceiptAuthority::new(SigningKey::from_bytes(&[3; 32])));
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let server = tokio::spawn(async move {
            axum::serve(listener, admission_router(config, receipts))
                .await
                .unwrap();
        });

        let call = |text: &'static str, token: &'static str| async move {
            let body = serde_json::json!({
                "kind": "user_message",
                "session_id": "session",
                "submission_id": "submission",
                "body": {
                    "schema_version": "openshell.pi-message.v1",
                    "origin": "user",
                    "text": text
                }
            })
            .to_string();
            let request = format!(
                "POST /v1/admission HTTP/1.1\r\nHost: {address}\r\nAuthorization: Bearer {token}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                body.len()
            );
            let mut stream = TcpStream::connect(address).await.unwrap();
            stream.write_all(request.as_bytes()).await.unwrap();
            let mut response = Vec::new();
            stream.read_to_end(&mut response).await.unwrap();
            String::from_utf8(response).unwrap()
        };

        assert!(
            call("plain text", "wrong")
                .await
                .starts_with("HTTP/1.1 401")
        );
        let allowed = call("plain text", "secret").await;
        assert!(allowed.starts_with("HTTP/1.1 200"));
        assert!(allowed.contains(r#""decision":"allow""#));
        let replaced = call("alice@example.com", "secret").await;
        assert!(replaced.contains(r#""decision":"replace""#));
        assert!(replaced.contains("[EMAIL]"));
        let denied = call("123-45-6789", "secret").await;
        assert!(denied.contains(r#""decision":"deny""#));

        server.abort();
        fs::remove_file(sandbox_id_file).unwrap();
    }
}
