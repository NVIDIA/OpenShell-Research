//! Standalone admission and egress enforcement for the Pi example.

mod admission;
mod auth;
mod policy;
mod receipt;

use std::{pin::Pin, sync::Arc};

use futures_core::Stream;
use tonic::{Request, Response, Status};

use auth::GatewayAuthentication;
use policy::{ProviderTarget, inspect_provider_request};
use receipt::ReceiptContext;

pub use admission::{AdmissionConfig, admission_router};
pub use receipt::ReceiptAuthority;

#[allow(clippy::large_enum_variant)]
pub mod pb {
    tonic::include_proto!("openshell.middleware.v1");
}

use pb::supervisor_middleware_server::{SupervisorMiddleware, SupervisorMiddlewareServer};

pub const SERVICE_NAME: &str = "pi-admission";
pub const SERVICE_VERSION: &str = "0.1.0";
pub const MAX_BODY_BYTES: u64 = 4 * 1024 * 1024;
pub const MAX_MESSAGE_BYTES: usize = MAX_BODY_BYTES as usize + 1024 * 1024;
pub const RECEIPT_HEADER: &str = "x-pi-admission-receipt";

#[derive(Clone)]
pub struct Middleware {
    authentication: Arc<GatewayAuthentication>,
    receipts: Arc<ReceiptAuthority>,
    config: Arc<AdmissionConfig>,
}

impl Middleware {
    pub fn from_config(
        config: Arc<AdmissionConfig>,
        receipts: Arc<ReceiptAuthority>,
    ) -> Result<Self, Box<dyn std::error::Error>> {
        let authentication = Arc::new(config.authentication()?);
        Ok(Self::new(authentication, receipts, config))
    }

    fn new(
        authentication: Arc<GatewayAuthentication>,
        receipts: Arc<ReceiptAuthority>,
        config: Arc<AdmissionConfig>,
    ) -> Self {
        Self {
            authentication,
            receipts,
            config,
        }
    }

    pub fn service(self) -> SupervisorMiddlewareServer<Self> {
        SupervisorMiddlewareServer::new(self)
            .max_decoding_message_size(MAX_MESSAGE_BYTES)
            .max_encoding_message_size(MAX_MESSAGE_BYTES)
    }

    fn manifest(&self) -> pb::MiddlewareManifest {
        pb::MiddlewareManifest {
            name: SERVICE_NAME.to_owned(),
            service_version: SERVICE_VERSION.to_owned(),
            bindings: vec![pb::MiddlewareBinding {
                operation: pb::SupervisorMiddlewareOperation::HttpRequest as i32,
                phase: pb::SupervisorMiddlewarePhase::PreCredentials as i32,
                max_payload_bytes: MAX_BODY_BYTES,
                timeout: String::new(),
            }],
            expected_audience: self.config.gateway_audience.clone(),
        }
    }

    fn deny(code: &str) -> pb::HttpRequestResult {
        pb::HttpRequestResult {
            decision: pb::Decision::Deny as i32,
            reason: "Pi admission denied the request".to_owned(),
            reason_code: code.to_owned(),
            ..Default::default()
        }
    }
}

#[tonic::async_trait]
impl SupervisorMiddleware for Middleware {
    type EvaluateWebSocketSessionStream = Pin<
        Box<dyn Stream<Item = Result<pb::WebSocketSessionEventResult, Status>> + Send + 'static>,
    >;

    async fn describe(
        &self,
        request: Request<()>,
    ) -> Result<Response<pb::MiddlewareManifest>, Status> {
        self.authentication
            .verify(request.metadata(), "gateway", None)?;
        Ok(Response::new(self.manifest()))
    }

    async fn validate_config(
        &self,
        request: Request<pb::ValidateConfigRequest>,
    ) -> Result<Response<pb::ValidateConfigResponse>, Status> {
        self.authentication
            .verify(request.metadata(), "gateway", None)?;
        let body = request.into_inner();
        let valid = body.middleware_name == self.config.middleware_name
            && body
                .config
                .as_ref()
                .is_none_or(|config| config.fields.is_empty());
        Ok(Response::new(pb::ValidateConfigResponse {
            valid,
            reason: if valid {
                String::new()
            } else {
                "the example accepts only its empty fixed-policy configuration".to_owned()
            },
        }))
    }

    async fn evaluate_http_request(
        &self,
        request: Request<pb::HttpRequestEvaluation>,
    ) -> Result<Response<pb::HttpRequestResult>, Status> {
        let sandbox_id = request
            .get_ref()
            .context
            .as_ref()
            .map(|context| context.sandbox_id.as_str())
            .filter(|value| !value.is_empty());
        self.authentication
            .verify(request.metadata(), "supervisor", sandbox_id)?;
        let request = request.into_inner();
        if request.phase != pb::SupervisorMiddlewarePhase::PreCredentials as i32 {
            return Ok(Response::new(Self::deny("unsupported_phase")));
        }
        if request.middleware_name != self.config.middleware_name {
            return Ok(Response::new(Self::deny("middleware_context_mismatch")));
        }
        let Some(context) = request.context else {
            return Ok(Response::new(Self::deny("request_context_missing")));
        };
        let Some(target) = request.target else {
            return Ok(Response::new(Self::deny("provider_shape_unsupported")));
        };
        let receipts: Vec<_> = request
            .headers
            .iter()
            .filter(|header| header.name.eq_ignore_ascii_case(RECEIPT_HEADER))
            .collect();
        if receipts.is_empty() {
            return Ok(Response::new(Self::deny("receipt_missing")));
        }
        if receipts.len() != 1 {
            return Ok(Response::new(Self::deny("receipt_malformed")));
        }
        let provider_target = ProviderTarget {
            scheme: target.scheme,
            host: target.host,
            port: target.port,
            method: target.method,
            path: target.path,
            query: target.query,
        };
        if provider_target != self.config.provider_target {
            return Ok(Response::new(Self::deny("receipt_context_mismatch")));
        }
        let projection = match inspect_provider_request(&request.body, &request.headers) {
            Ok(projection) => projection,
            Err(code) => return Ok(Response::new(Self::deny(code))),
        };
        let receipt_context = ReceiptContext {
            middleware_name: &self.config.middleware_name,
            sandbox_id: &context.sandbox_id,
            target: &provider_target,
        };
        if let Err(code) =
            self.receipts
                .verify_header(&receipts[0].value, &projection, receipt_context)
        {
            return Ok(Response::new(Self::deny(code)));
        }
        Ok(Response::new(pb::HttpRequestResult {
            decision: pb::Decision::Allow as i32,
            header_mutations: vec![pb::HeaderMutation {
                operation: Some(pb::header_mutation::Operation::Remove(pb::RemoveHeader {
                    name: RECEIPT_HEADER.to_owned(),
                })),
            }],
            ..Default::default()
        }))
    }

    async fn evaluate_web_socket_session(
        &self,
        _request: Request<tonic::Streaming<pb::WebSocketSessionEvent>>,
    ) -> Result<Response<Self::EvaluateWebSocketSessionStream>, Status> {
        Err(Status::unimplemented(
            "Pi admission supports HTTP requests only",
        ))
    }
}
