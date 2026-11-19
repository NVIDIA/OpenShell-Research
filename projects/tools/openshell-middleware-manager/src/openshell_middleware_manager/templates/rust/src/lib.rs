//! Pass-through OpenShell supervisor middleware service.

use std::pin::Pin;

use tokio_stream::{Stream, StreamExt};
use tonic::{Request, Response, Status, Streaming};

mod proto {
    pub mod extension {
        pub mod v1 {
            tonic::include_proto!("openshell.extension.v1");
        }
    }
    pub mod middleware {
        pub mod v1 {
            tonic::include_proto!("openshell.middleware.v1");
        }
    }
}

pub use proto::extension::v1 as extension;
pub use proto::middleware::v1 as pb;

use pb::http_hooks_server::{HttpHooks, HttpHooksServer};
use pb::supervisor_middleware_server::{SupervisorMiddleware, SupervisorMiddlewareServer};

pub const SERVICE_NAME: &str = "__SERVICE_NAME__";
pub const SERVICE_VERSION: &str = "0.1.0";
pub const PROTOCOL_MAJOR: u32 = 1;
pub const PROTOCOL_MINOR: u32 = 0;
pub const CONTRACT_CAPABILITY: &str = "openshell.supervisor-middleware.contract";
pub const HTTP_HOOKS_CAPABILITY: &str = "openshell.supervisor-middleware.http-hooks";
pub const CAPABILITIES: [&str; 2] = [CONTRACT_CAPABILITY, HTTP_HOOKS_CAPABILITY];
pub const MAX_PAYLOAD_BYTES: u64 = 4 * 1024 * 1024;
pub const MAX_MESSAGE_BYTES: usize = MAX_PAYLOAD_BYTES as usize + 1024 * 1024;

/// Ordered results that a streaming RPC returns to OpenShell.
pub type ResultStream<T> = Pin<Box<dyn Stream<Item = Result<T, Status>> + Send>>;

#[derive(Debug, Default)]
pub struct Middleware;

pub fn middleware_service() -> SupervisorMiddlewareServer<Middleware> {
    SupervisorMiddlewareServer::new(Middleware)
        .max_decoding_message_size(MAX_MESSAGE_BYTES)
        .max_encoding_message_size(MAX_MESSAGE_BYTES)
}

pub fn http_hooks_service() -> HttpHooksServer<Middleware> {
    HttpHooksServer::new(Middleware)
        .max_decoding_message_size(MAX_MESSAGE_BYTES)
        .max_encoding_message_size(MAX_MESSAGE_BYTES)
}

pub fn build_manifest() -> pb::MiddlewareManifest {
    let capabilities: Vec<String> = CAPABILITIES.iter().map(ToString::to_string).collect();
    pb::MiddlewareManifest {
        name: SERVICE_NAME.to_owned(),
        bindings: vec![pb::MiddlewareBinding {
            operation: pb::SupervisorMiddlewareOperation::HttpRequest as i32,
            phase: pb::SupervisorMiddlewarePhase::PreCredentials as i32,
            max_payload_bytes: MAX_PAYLOAD_BYTES,
            ..Default::default()
        }],
        // Requiring the HTTP hooks capability selects HttpHooks for the HTTP binding.
        extension: Some(extension::PeerMetadata {
            protocol_version: Some(extension::ProtocolVersion {
                major: PROTOCOL_MAJOR,
                minor: PROTOCOL_MINOR,
            }),
            implementation_name: SERVICE_NAME.to_owned(),
            implementation_version: SERVICE_VERSION.to_owned(),
            supported_capabilities: capabilities.clone(),
            required_capabilities: capabilities,
        }),
        ..Default::default()
    }
}

/// Explain why the calling gateway or supervisor cannot use this service.
pub fn find_protocol_mismatch(request: &pb::MiddlewareDescribeRequest) -> Option<String> {
    let Some(caller) = &request.gateway else {
        return Some("the caller did not provide protocol metadata".to_owned());
    };
    let major = caller
        .protocol_version
        .as_ref()
        .map_or(0, |version| version.major);
    if major != PROTOCOL_MAJOR {
        return Some(format!(
            "the caller uses unsupported protocol major {major}"
        ));
    }
    let missing: Vec<&str> = CAPABILITIES
        .into_iter()
        .filter(|capability| {
            !caller
                .supported_capabilities
                .iter()
                .any(|c| c == capability)
        })
        .collect();
    if !missing.is_empty() {
        return Some(format!(
            "the caller does not support required capabilities: {}",
            missing.join(", ")
        ));
    }
    let unsupported: Vec<&str> = caller
        .required_capabilities
        .iter()
        .map(String::as_str)
        .filter(|capability| !CAPABILITIES.contains(capability))
        .collect();
    if !unsupported.is_empty() {
        return Some(format!(
            "the caller requires unsupported capabilities: {}",
            unsupported.join(", ")
        ));
    }
    None
}

pub fn validate_config(_request: pb::ValidateConfigRequest) -> pb::ValidateConfigResponse {
    pb::ValidateConfigResponse {
        valid: true,
        reason: String::new(),
    }
}

/// Decide from the request head whether to continue, deny, or inspect the body.
pub fn evaluate_http_request_preflight(
    preflight: pb::HttpRequestHookPreflight,
) -> pb::HttpRequestHookResult {
    use pb::http_request_hook_result::Result as HookResult;

    if preflight.phase != pb::SupervisorMiddlewarePhase::PreCredentials as i32 {
        return pb::HttpRequestHookResult {
            result: Some(HookResult::Deny(pb::HttpDenial {
                diagnostics: Some(pb::MiddlewareDiagnostics {
                    reason: "unsupported middleware phase".to_owned(),
                    reason_code: "unsupported_phase".to_owned(),
                    ..Default::default()
                }),
            })),
        };
    }
    pb::HttpRequestHookResult {
        result: Some(HookResult::PreflightResult(pb::HttpPreflightResult {
            decision: Some(pb::http_preflight_result::Decision::ContinueWithoutBody(
                pb::HttpContinue {},
            )),
            ..Default::default()
        })),
    }
}

/// Keep, replace, or deny a complete body after preflight selected whole-body inspection.
pub fn evaluate_http_request_body(_body: pb::HttpWholeBody) -> pb::HttpRequestHookResult {
    pb::HttpRequestHookResult {
        result: Some(pb::http_request_hook_result::Result::WholeBodyResult(
            pb::HttpWholeBodyResult {
                body: Some(pb::http_whole_body_result::Body::Unchanged(
                    pb::HttpUnchanged {},
                )),
                ..Default::default()
            },
        )),
    }
}

/// Answer one hook stream event, or return `None` for an event that takes no result.
pub fn evaluate_http_request_event(
    event: pb::HttpRequestHookEvent,
) -> Option<pb::HttpRequestHookResult> {
    use pb::http_request_hook_event::Event;

    match event.event? {
        Event::Preflight(preflight) => Some(evaluate_http_request_preflight(preflight)),
        Event::WholeBody(body) => Some(evaluate_http_request_body(body)),
        _ => None,
    }
}

#[tonic::async_trait]
impl SupervisorMiddleware for Middleware {
    async fn describe(
        &self,
        request: Request<pb::MiddlewareDescribeRequest>,
    ) -> Result<Response<pb::MiddlewareManifest>, Status> {
        if let Some(mismatch) = find_protocol_mismatch(request.get_ref()) {
            return Err(Status::failed_precondition(mismatch));
        }
        Ok(Response::new(build_manifest()))
    }

    async fn validate_config(
        &self,
        request: Request<pb::ValidateConfigRequest>,
    ) -> Result<Response<pb::ValidateConfigResponse>, Status> {
        Ok(Response::new(validate_config(request.into_inner())))
    }
}

#[tonic::async_trait]
impl HttpHooks for Middleware {
    async fn evaluate_http_request_hook(
        &self,
        request: Request<Streaming<pb::HttpRequestHookEvent>>,
    ) -> Result<Response<ResultStream<pb::HttpRequestHookResult>>, Status> {
        let results = request.into_inner().filter_map(|event| match event {
            Ok(event) => evaluate_http_request_event(event).map(Ok),
            Err(status) => Some(Err(status)),
        });
        Ok(Response::new(Box::pin(results)))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use pb::http_hooks_client::HttpHooksClient;
    use pb::http_request_hook_event::Event;
    use pb::http_request_hook_result::Result as HookResult;
    use pb::supervisor_middleware_client::SupervisorMiddlewareClient;
    use tokio::net::TcpListener;
    use tokio::task::JoinHandle;
    use tokio_stream::wrappers::TcpListenerStream;
    use tonic::Code;
    use tonic::transport::Server;

    fn caller_metadata(
        major: u32,
        supported: &[&str],
        required: &[&str],
    ) -> pb::MiddlewareDescribeRequest {
        pb::MiddlewareDescribeRequest {
            gateway: Some(extension::PeerMetadata {
                protocol_version: Some(extension::ProtocolVersion { major, minor: 0 }),
                implementation_name: "openshell/gateway".to_owned(),
                implementation_version: "test".to_owned(),
                supported_capabilities: supported.iter().map(ToString::to_string).collect(),
                required_capabilities: required.iter().map(ToString::to_string).collect(),
            }),
        }
    }

    fn compatible_caller() -> pb::MiddlewareDescribeRequest {
        caller_metadata(PROTOCOL_MAJOR, &CAPABILITIES, &[CONTRACT_CAPABILITY])
    }

    async fn serve() -> (String, JoinHandle<()>) {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let server = tokio::spawn(async move {
            Server::builder()
                .add_service(middleware_service())
                .add_service(http_hooks_service())
                .serve_with_incoming(TcpListenerStream::new(listener))
                .await
                .unwrap();
        });
        (format!("http://{address}"), server)
    }

    #[test]
    fn manifest_advertises_pre_credentials_http_hooks() {
        let manifest = build_manifest();
        assert_eq!(manifest.name, SERVICE_NAME);
        assert_eq!(manifest.bindings.len(), 1);
        assert_eq!(
            manifest.bindings[0].operation,
            pb::SupervisorMiddlewareOperation::HttpRequest as i32
        );
        assert_eq!(
            manifest.bindings[0].phase,
            pb::SupervisorMiddlewarePhase::PreCredentials as i32
        );
        assert_eq!(manifest.bindings[0].max_payload_bytes, MAX_PAYLOAD_BYTES);
        let extension = manifest.extension.unwrap();
        assert_eq!(
            extension.protocol_version,
            Some(extension::ProtocolVersion { major: 1, minor: 0 })
        );
        assert_eq!(extension.implementation_name, SERVICE_NAME);
        assert_eq!(extension.supported_capabilities, CAPABILITIES);
        assert_eq!(extension.required_capabilities, CAPABILITIES);
    }

    #[test]
    fn compatible_caller_has_no_protocol_mismatch() {
        assert_eq!(find_protocol_mismatch(&compatible_caller()), None);
    }

    #[test]
    fn incompatible_caller_is_explained() {
        let cases = [
            (
                pb::MiddlewareDescribeRequest::default(),
                "did not provide protocol metadata",
            ),
            (
                caller_metadata(2, &CAPABILITIES, &[CONTRACT_CAPABILITY]),
                "unsupported protocol major 2",
            ),
            (
                caller_metadata(1, &[CONTRACT_CAPABILITY], &[CONTRACT_CAPABILITY]),
                HTTP_HOOKS_CAPABILITY,
            ),
            (
                caller_metadata(1, &CAPABILITIES, &["example.unknown"]),
                "example.unknown",
            ),
        ];
        for (request, reason) in cases {
            let mismatch = find_protocol_mismatch(&request).unwrap();
            assert!(mismatch.contains(reason), "{mismatch}");
        }
    }

    #[test]
    fn default_config_is_valid() {
        let response = validate_config(pb::ValidateConfigRequest::default());
        assert!(response.valid);
        assert!(response.reason.is_empty());
    }

    #[test]
    fn valid_request_continues_without_mutation() {
        let result = evaluate_http_request_preflight(pb::HttpRequestHookPreflight {
            phase: pb::SupervisorMiddlewarePhase::PreCredentials as i32,
            ..Default::default()
        });
        let Some(HookResult::PreflightResult(preflight_result)) = result.result else {
            panic!("expected a preflight result");
        };
        assert_eq!(
            preflight_result.decision,
            Some(pb::http_preflight_result::Decision::ContinueWithoutBody(
                pb::HttpContinue {}
            ))
        );
        assert!(preflight_result.header_mutations.is_empty());
    }

    #[test]
    fn unsupported_phase_is_denied() {
        let result = evaluate_http_request_preflight(pb::HttpRequestHookPreflight::default());
        let Some(HookResult::Deny(denial)) = result.result else {
            panic!("expected a denial");
        };
        assert_eq!(denial.diagnostics.unwrap().reason_code, "unsupported_phase");
    }

    #[test]
    fn whole_body_is_kept_unchanged() {
        let result = evaluate_http_request_body(pb::HttpWholeBody {
            data: b"request body".to_vec(),
            ..Default::default()
        });
        let Some(HookResult::WholeBodyResult(body_result)) = result.result else {
            panic!("expected a whole-body result");
        };
        assert_eq!(
            body_result.body,
            Some(pb::http_whole_body_result::Body::Unchanged(
                pb::HttpUnchanged {}
            ))
        );
    }

    #[test]
    fn events_without_results_are_ignored() {
        for event in [
            Event::Begin(pb::HttpBegin::default()),
            Event::SessionEnd(pb::MiddlewareSessionEnd::default()),
        ] {
            let result =
                evaluate_http_request_event(pb::HttpRequestHookEvent { event: Some(event) });
            assert_eq!(result, None);
        }
    }

    #[tokio::test]
    async fn describe_negotiates_with_the_caller() {
        let (endpoint, server) = serve().await;
        let mut client = SupervisorMiddlewareClient::connect(endpoint).await.unwrap();

        let manifest = client.describe(compatible_caller()).await.unwrap();
        assert_eq!(manifest.into_inner().name, SERVICE_NAME);

        let status = client
            .describe(pb::MiddlewareDescribeRequest::default())
            .await
            .unwrap_err();
        assert_eq!(status.code(), Code::FailedPrecondition);
        server.abort();
    }

    #[tokio::test]
    async fn hook_stream_answers_preflight_and_maximum_body() {
        let (endpoint, server) = serve().await;
        let mut client = HttpHooksClient::connect(endpoint)
            .await
            .unwrap()
            .max_encoding_message_size(MAX_MESSAGE_BYTES)
            .max_decoding_message_size(MAX_MESSAGE_BYTES);
        let events = [
            Event::Preflight(pb::HttpRequestHookPreflight {
                phase: pb::SupervisorMiddlewarePhase::PreCredentials as i32,
                context: Some(pb::RequestContext {
                    request_id: "max-body-request".to_owned(),
                    sandbox_id: "max-body-sandbox".to_owned(),
                    ..Default::default()
                }),
                middleware_name: SERVICE_NAME.to_owned(),
                request: Some(pb::HttpRequestPreflightHead {
                    headers: vec![pb::HttpHeader {
                        name: "content-type".to_owned(),
                        value: "application/octet-stream".to_owned(),
                    }],
                    ..Default::default()
                }),
                ..Default::default()
            }),
            Event::Begin(pb::HttpBegin::default()),
            Event::WholeBody(pb::HttpWholeBody {
                data: vec![b'x'; MAX_PAYLOAD_BYTES as usize],
                ..Default::default()
            }),
            Event::SessionEnd(pb::MiddlewareSessionEnd::default()),
        ]
        .map(|event| pb::HttpRequestHookEvent { event: Some(event) });

        let mut results = client
            .evaluate_http_request_hook(tokio_stream::iter(events))
            .await
            .unwrap()
            .into_inner();
        let mut kinds = Vec::new();
        while let Some(result) = results.message().await.unwrap() {
            kinds.push(match result.result {
                Some(HookResult::PreflightResult(_)) => "preflight_result",
                Some(HookResult::WholeBodyResult(_)) => "whole_body_result",
                _ => "unexpected",
            });
        }

        assert_eq!(kinds, ["preflight_result", "whole_body_result"]);
        server.abort();
    }
}
