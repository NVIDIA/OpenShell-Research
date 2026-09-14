use std::{collections::BTreeSet, sync::LazyLock};

use regex::Regex;
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

use crate::pb;

pub(crate) const POLICY_ID: &str = "pi-admission-fixed-regex.v1";

static EMAIL: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?i)\b[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@example\.com\b").unwrap());
static SSN: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"\b[0-9]{3}-[0-9]{2}-[0-9]{4}\b").unwrap());

#[derive(Clone, Debug, Deserialize, PartialEq, Eq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ProviderTarget {
    pub scheme: String,
    pub host: String,
    pub port: u32,
    pub method: String,
    pub path: String,
    pub query: String,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Eq, Serialize)]
#[serde(tag = "role", deny_unknown_fields)]
pub(crate) enum ContextEntry {
    #[serde(rename = "user")]
    User { text: String },
    #[serde(rename = "tool")]
    Tool { tool_call_id: String, text: String },
}

pub(crate) type Projection = Vec<ContextEntry>;

pub(crate) enum CandidateDecision {
    Allow,
    Replace(Value),
    Deny(&'static str),
}

pub(crate) fn evaluate_candidate(kind: &str, mut body: Value) -> CandidateDecision {
    if contains_match(&body, &SSN) {
        return CandidateDecision::Deny("ssn_detected");
    }
    let result = match kind {
        "user_message" => edit_message(&mut body, "user"),
        "system_context" => edit_message(&mut body, "system"),
        "compaction_summary" => edit_message(&mut body, "compaction_summary"),
        "tool_result" => edit_tool_result(&mut body),
        "assistant_message" => edit_assistant(&mut body),
        "provider_context" => validate_provider_context(&body),
        _ => Err("admission_contract_invalid"),
    };
    match result {
        Err(code) => CandidateDecision::Deny(code),
        Ok(false) => CandidateDecision::Allow,
        Ok(true) => CandidateDecision::Replace(body),
    }
}

pub(crate) fn inspect_provider_request(
    body: &[u8],
    headers: &[pb::HttpHeader],
) -> Result<Projection, &'static str> {
    let content_types: Vec<_> = headers
        .iter()
        .filter(|header| header.name.eq_ignore_ascii_case("content-type"))
        .map(|header| header.value.trim().to_ascii_lowercase())
        .collect();
    if content_types != ["application/json"]
        || headers
            .iter()
            .any(|header| header.name.eq_ignore_ascii_case("content-encoding"))
    {
        return Err("provider_shape_unsupported");
    }
    let value: Value = serde_json::from_slice(body).map_err(|_| "provider_shape_unsupported")?;
    if contains_match(&value, &SSN) || contains_match(&value, &EMAIL) {
        return Err("entity_detected_at_egress");
    }
    let object = value.as_object().ok_or("provider_shape_unsupported")?;
    exact_keys(
        object,
        &[
            "model",
            "messages",
            "tools",
            "tool_choice",
            "temperature",
            "top_p",
            "max_completion_tokens",
            "max_tokens",
            "stream",
            "stream_options",
            "store",
            "prompt_cache_key",
            "prompt_cache_retention",
            "reasoning_effort",
            "reasoning",
            "enable_thinking",
        ],
    )?;
    if object.get("model").and_then(Value::as_str).is_none()
        || object.get("stream") != Some(&Value::Bool(true))
        || (object.contains_key("max_tokens") == object.contains_key("max_completion_tokens"))
    {
        return Err("provider_shape_unsupported");
    }
    let messages = object
        .get("messages")
        .and_then(Value::as_array)
        .ok_or("provider_shape_unsupported")?;
    let mut projection = Vec::new();
    for message in messages {
        let message = message.as_object().ok_or("provider_shape_unsupported")?;
        exact_keys(
            message,
            &[
                "role",
                "content",
                "name",
                "tool_call_id",
                "tool_calls",
                "reasoning_content",
                "reasoning",
                "reasoning_text",
                "reasoning_details",
            ],
        )?;
        let role = message
            .get("role")
            .and_then(Value::as_str)
            .ok_or("provider_shape_unsupported")?;
        let content = message.get("content").ok_or("provider_shape_unsupported")?;
        let text = provider_content(content)?;
        match role {
            "user" => {
                if let Some(text) = text {
                    projection.push(ContextEntry::User { text });
                }
            }
            "tool" => {
                let id = message
                    .get("tool_call_id")
                    .and_then(Value::as_str)
                    .ok_or("provider_shape_unsupported")?;
                let text = text.ok_or("provider_shape_unsupported")?;
                projection.push(ContextEntry::Tool {
                    tool_call_id: id.split('|').next().unwrap_or(id).to_owned(),
                    text,
                });
            }
            "system" | "developer" | "assistant" => {}
            _ => return Err("provider_shape_unsupported"),
        }
    }
    if projection.is_empty() {
        return Err("provider_shape_unsupported");
    }
    Ok(projection)
}

fn edit_message(body: &mut Value, origin: &str) -> Result<bool, &'static str> {
    let object = shape(body, &["schema_version", "origin", "text"])?;
    if object.get("schema_version").and_then(Value::as_str) != Some("openshell.pi-message.v1")
        || object.get("origin").and_then(Value::as_str) != Some(origin)
    {
        return Err("admission_contract_invalid");
    }
    replace_field(object, "text")
}

fn edit_tool_result(body: &mut Value) -> Result<bool, &'static str> {
    let object = shape(
        body,
        &[
            "schema_version",
            "tool_call_id",
            "tool_name",
            "content",
            "is_error",
        ],
    )?;
    if object.get("schema_version").and_then(Value::as_str) != Some("openshell.pi-tool-result.v1")
        || object.get("tool_call_id").and_then(Value::as_str).is_none()
        || object.get("tool_name").and_then(Value::as_str).is_none()
        || object.get("is_error").and_then(Value::as_bool).is_none()
    {
        return Err("admission_contract_invalid");
    }
    let blocks = object
        .get_mut("content")
        .and_then(Value::as_array_mut)
        .ok_or("admission_contract_invalid")?;
    let mut changed = false;
    for block in blocks {
        let block = shape(block, &["type", "text"])?;
        if block.get("type").and_then(Value::as_str) != Some("text") {
            return Err("admission_contract_invalid");
        }
        changed |= replace_field(block, "text")?;
    }
    Ok(changed)
}

fn edit_assistant(body: &mut Value) -> Result<bool, &'static str> {
    let object = shape(body, &["schema_version", "text", "tool_calls", "thinking"])?;
    if object.get("schema_version").and_then(Value::as_str)
        != Some("openshell.pi-assistant-message.v1")
    {
        return Err("admission_contract_invalid");
    }
    if contains_match(
        object
            .get("tool_calls")
            .ok_or("admission_contract_invalid")?,
        &EMAIL,
    ) {
        return Err("immutable_content_detected");
    }
    let mut changed = replace_field(object, "text")?;
    let thinking = object
        .get_mut("thinking")
        .and_then(Value::as_array_mut)
        .ok_or("admission_contract_invalid")?;
    for block in thinking {
        let block = shape(block, &["text", "signature"])?;
        let redactable = block
            .get("text")
            .and_then(Value::as_str)
            .is_some_and(|text| EMAIL.is_match(text));
        let signature = block.get("signature").ok_or("admission_contract_invalid")?;
        let editable = signature.is_null()
            || matches!(
                signature.as_str(),
                Some("reasoning" | "reasoning_content" | "reasoning_text")
            );
        if redactable && !editable {
            return Err("immutable_content_detected");
        }
        if editable {
            changed |= replace_field(block, "text")?;
        }
    }
    Ok(changed)
}

fn validate_provider_context(body: &Value) -> Result<bool, &'static str> {
    let object = body.as_object().ok_or("admission_contract_invalid")?;
    exact_keys(object, &["schema_version", "entries"])?;
    if object.get("schema_version").and_then(Value::as_str)
        != Some("openshell.pi-provider-context.v1")
    {
        return Err("admission_contract_invalid");
    }
    let entries: Projection = serde_json::from_value(
        object
            .get("entries")
            .cloned()
            .ok_or("admission_contract_invalid")?,
    )
    .map_err(|_| "admission_contract_invalid")?;
    if entries.is_empty() {
        return Err("admission_contract_invalid");
    }
    if contains_match(body, &EMAIL) {
        return Err("email_detected_at_receipt");
    }
    Ok(false)
}

fn provider_content(value: &Value) -> Result<Option<String>, &'static str> {
    if value.is_null() {
        return Ok(None);
    }
    if let Some(text) = value.as_str() {
        return Ok(Some(text.to_owned()));
    }
    let blocks = value.as_array().ok_or("provider_shape_unsupported")?;
    let mut text = Vec::new();
    for block in blocks {
        let block = block.as_object().ok_or("provider_shape_unsupported")?;
        exact_keys(block, &["type", "text", "cache_control"])?;
        if block.get("type").and_then(Value::as_str) != Some("text") {
            return Err("provider_shape_unsupported");
        }
        text.push(
            block
                .get("text")
                .and_then(Value::as_str)
                .ok_or("provider_shape_unsupported")?,
        );
    }
    Ok(Some(text.join("\n")))
}

fn replace_field(object: &mut Map<String, Value>, key: &str) -> Result<bool, &'static str> {
    let value = object
        .get_mut(key)
        .and_then(|value| value.as_str())
        .ok_or("admission_contract_invalid")?;
    let replacement = EMAIL.replace_all(value, "[EMAIL]");
    if replacement == value {
        return Ok(false);
    }
    *object.get_mut(key).unwrap() = Value::String(replacement.into_owned());
    Ok(true)
}

fn shape<'a>(
    value: &'a mut Value,
    keys: &[&str],
) -> Result<&'a mut Map<String, Value>, &'static str> {
    let object = value.as_object_mut().ok_or("admission_contract_invalid")?;
    exact_keys(object, keys)?;
    Ok(object)
}

fn exact_keys(object: &Map<String, Value>, allowed: &[&str]) -> Result<(), &'static str> {
    let allowed: BTreeSet<_> = allowed.iter().copied().collect();
    if object.keys().any(|key| !allowed.contains(key.as_str())) {
        return Err("provider_shape_unsupported");
    }
    Ok(())
}

fn contains_match(value: &Value, pattern: &Regex) -> bool {
    match value {
        Value::String(text) => pattern.is_match(text),
        Value::Array(values) => values.iter().any(|value| contains_match(value, pattern)),
        Value::Object(values) => values.values().any(|value| contains_match(value, pattern)),
        _ => false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn egress_allows_approved_content_and_rejects_decoded_entities() {
        let headers = [pb::HttpHeader {
            name: "content-type".to_owned(),
            value: "application/json".to_owned(),
        }];
        let allowed = br#"{"model":"demo","messages":[{"role":"user","content":"[EMAIL]"}],"max_tokens":10,"stream":true}"#;
        assert_eq!(
            inspect_provider_request(allowed, &headers).unwrap(),
            vec![ContextEntry::User {
                text: "[EMAIL]".to_owned()
            }]
        );
        let escaped = br#"{"model":"demo","messages":[{"role":"user","content":"alice\u0040example.com"}],"max_tokens":10,"stream":true}"#;
        assert_eq!(
            inspect_provider_request(escaped, &headers),
            Err("entity_detected_at_egress")
        );
    }
}
