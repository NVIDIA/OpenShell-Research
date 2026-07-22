//! Pass-through OpenShell supervisor middleware service.

use tonic::{Request, Response, Status};

pub mod pb {
    tonic::include_proto!("openshell.middleware.v1");
}

use pb::supervisor_middleware_server::SupervisorMiddleware;

pub const SERVICE_NAME: &str = "__SERVICE_NAME__";
pub const SERVICE_VERSION: &str = "0.1.0";
pub const MAX_BODY_BYTES: u64 = 4 * 1024 * 1024;

#[derive(Debug, Default)]
pub struct Middleware;

pub fn build_manifest() -> pb::MiddlewareManifest {
    pb::MiddlewareManifest {
        name: SERVICE_NAME.to_owned(),
        service_version: SERVICE_VERSION.to_owned(),
        bindings: vec![pb::MiddlewareBinding {
            operation: pb::SupervisorMiddlewareOperation::HttpRequest as i32,
            phase: pb::SupervisorMiddlewarePhase::PreCredentials as i32,
            max_body_bytes: MAX_BODY_BYTES,
            timeout: String::new(),
        }],
    }
}

pub fn validate_config(_request: pb::ValidateConfigRequest) -> pb::ValidateConfigResponse {
    pb::ValidateConfigResponse {
        valid: true,
        reason: String::new(),
    }
}

pub fn evaluate_http_request(request: pb::HttpRequestEvaluation) -> pb::HttpRequestResult {
    if request.phase != pb::SupervisorMiddlewarePhase::PreCredentials as i32 {
        return pb::HttpRequestResult {
            decision: pb::Decision::Deny as i32,
            reason: "unsupported middleware phase".to_owned(),
            reason_code: "unsupported_phase".to_owned(),
            ..Default::default()
        };
    }
    pb::HttpRequestResult {
        decision: pb::Decision::Allow as i32,
        ..Default::default()
    }
}

#[tonic::async_trait]
impl SupervisorMiddleware for Middleware {
    async fn describe(
        &self,
        _request: Request<()>,
    ) -> Result<Response<pb::MiddlewareManifest>, Status> {
        Ok(Response::new(build_manifest()))
    }

    async fn validate_config(
        &self,
        request: Request<pb::ValidateConfigRequest>,
    ) -> Result<Response<pb::ValidateConfigResponse>, Status> {
        Ok(Response::new(validate_config(request.into_inner())))
    }

    async fn evaluate_http_request(
        &self,
        request: Request<pb::HttpRequestEvaluation>,
    ) -> Result<Response<pb::HttpRequestResult>, Status> {
        Ok(Response::new(evaluate_http_request(request.into_inner())))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn manifest_advertises_pre_credentials_http() {
        let manifest = build_manifest();
        assert_eq!(manifest.name, SERVICE_NAME);
        assert_eq!(manifest.bindings.len(), 1);
        assert_eq!(manifest.bindings[0].max_body_bytes, MAX_BODY_BYTES);
    }

    #[test]
    fn valid_request_is_allowed_without_mutation() {
        let response = evaluate_http_request(pb::HttpRequestEvaluation {
            phase: pb::SupervisorMiddlewarePhase::PreCredentials as i32,
            ..Default::default()
        });
        assert_eq!(response.decision, pb::Decision::Allow as i32);
        assert!(!response.has_body);
        assert!(response.header_mutations.is_empty());
    }

    #[test]
    fn unsupported_phase_is_denied() {
        let response = evaluate_http_request(pb::HttpRequestEvaluation::default());
        assert_eq!(response.decision, pb::Decision::Deny as i32);
        assert_eq!(response.reason_code, "unsupported_phase");
    }
}
