# frozen_string_literal: true
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
# SPDX-License-Identifier: Apache-2.0

require "json"
require "digest"
require "time"

MAX_REQUEST_BYTES = 4 * 1024 * 1024
MAX_EVENT_BYTES = 1024 * 1024
MAX_EVENTS = 500
EXPECTED_CONTENT_TYPE = "application/cloudevents-batch+json"
KNOWN_CE_FIELDS = %w[specversion id source type dataschema datacontenttype subject time data].freeze

def register(_params)
end

def request_content_type(event)
  value = event.get("[http][request][mime_type]")
  return value unless value.nil? || value.empty?

  headers = event.get("[@metadata][input][http][request][headers]")
  if headers.is_a?(Hash)
    value = headers["content-type"] || headers["content_type"]
    return value unless value.nil? || value.empty?
  end

  legacy_headers = event.get("headers")
  return nil unless legacy_headers.is_a?(Hash)

  legacy_headers["content-type"] || legacy_headers["content_type"]
end

def normalized_media_type(value)
  return nil unless value.is_a?(String)

  value.split(";", 2).first.strip.downcase
end

def dig_hash(value, *path)
  path.reduce(value) { |memo, key| memo.is_a?(Hash) ? memo[key] : nil }
end

def present(value)
  return nil if value.nil? || value == ""

  value
end

def integer(value)
  return value if value.is_a?(Integer)
  return value.to_i if value.is_a?(Float) && value.finite? && value == value.to_i

  Integer(value, 10)
rescue ArgumentError, TypeError
  nil
end

def put_path(target, path, value)
  value = present(value)
  return if value.nil?

  cursor = target
  path[0...-1].each do |key|
    cursor[key] = {} unless cursor[key].is_a?(Hash)
    cursor = cursor[key]
  end
  cursor[path[-1]] = value
end

def first_value(source, *paths)
  paths.each do |path|
    value = dig_hash(source, *path)
    return value unless present(value).nil?
  end
  nil
end

def extensions(event)
  event.each_with_object({}) do |(key, value), result|
    result[key] = value unless KNOWN_CE_FIELDS.include?(key)
  end
end

def validate_event(event)
  return "event must be a JSON object" unless event.is_a?(Hash)
  return "specversion must be 1.0" unless event["specversion"] == "1.0"

  %w[id source type].each do |field|
    return "#{field} is required" unless event[field].is_a?(String) && !event[field].empty?
  end
  return "unsupported dataschema" unless event["dataschema"] == "urn:openshell:event-envelope:1"
  return "unsupported datacontenttype" unless event["datacontenttype"] == "application/json"
  return "data must be an object" unless event["data"].is_a?(Hash)
  return "unsupported data.schema_version" unless event.dig("data", "schema_version") == "1.0"

  nil
end

def event_categories(event_type, ocsf)
  class_uid = ocsf["class_uid"].to_s
  return ["network"] if %w[4001 4002 4007].include?(class_uid) || event_type.include?("gateway.log")
  return ["process"] if class_uid.start_with?("1")
  return ["configuration"] if class_uid.start_with?("5")
  return ["host"] if class_uid.start_with?("6")

  ["intrusion_detection"]
end

def classify(event_type, message, ocsf, validation)
  tags = ["openshell", "security-evidence"]
  tags << "ocsf" if event_type.include?(".ocsf.")
  status = validation["status"].to_s
  return ["unknown", ["error"], tags + ["validation-failure"]] if status == "invalid"
  if event_type.include?(".ocsf.") && status == "valid" && ocsf["class_uid"].to_s == "4001" && message.downcase.include?("denied")
    return ["failure", ["denied"], tags + ["policy-denial"]]
  end
  return ["unknown", ["error"], tags + ["source-gap"]] if event_type.include?(".stream.warning.")
  return ["success", ["change"], tags + ["policy-change"]] if event_type.include?(".policy.draft_updated.")
  return ["unknown", ["info"], tags + ["sandbox-lifecycle"]] if event_type.include?(".sandbox.lifecycle.")

  ["unknown", ["info"], tags]
end

def promote_process(document, original)
  actor = dig_hash(original, "actor") || {}
  process = dig_hash(actor, "process") || dig_hash(original, "process") || {}
  parent = dig_hash(actor, "process", "parent_process") || dig_hash(original, "parent_process") || {}
  put_path(document, %w[process executable], first_value(process, ["file", "path"], ["executable"], ["path"]))
  put_path(document, %w[process name], first_value(process, ["name"], ["file", "name"]))
  put_path(document, %w[process pid], integer(process["pid"]))
  put_path(document, %w[process command_line], process["cmd_line"] || process["command_line"])
  put_path(document, %w[process parent executable], first_value(parent, ["file", "path"], ["executable"], ["path"]))
  put_path(document, %w[process parent name], first_value(parent, ["name"], ["file", "name"]))
  put_path(document, %w[process parent pid], integer(parent["pid"]))
end

def promote_network(document, original)
  source = dig_hash(original, "src_endpoint") || dig_hash(original, "source") || {}
  destination = dig_hash(original, "dst_endpoint") || dig_hash(original, "destination") || {}
  put_path(document, %w[source ip], source["ip"])
  put_path(document, %w[source port], integer(source["port"]))
  put_path(document, %w[source address], first_value(source, ["ip"], ["domain"], ["hostname"], ["name"]))
  put_path(document, %w[source domain], first_value(source, ["domain"], ["hostname"]))
  put_path(document, %w[destination ip], destination["ip"])
  put_path(document, %w[destination port], integer(destination["port"]))
  put_path(document, %w[destination address], first_value(destination, ["ip"], ["domain"], ["hostname"], ["name"]))
  put_path(document, %w[destination domain], first_value(destination, ["domain"], ["hostname"]))
  put_path(document, %w[network transport], first_value(original, ["connection_info", "protocol_name"], ["network", "transport"]))
  put_path(document, %w[network protocol], first_value(original, ["protocol_name"], ["network", "protocol"]))
  put_path(document, %w[network direction], first_value(original, ["direction"], ["network", "direction"]))
end

def promote_http(document, original)
  request = dig_hash(original, "http_request") || {}
  url = dig_hash(request, "url") || dig_hash(original, "url") || {}
  put_path(document, %w[http request method], request["http_method"] || request["method"])
  put_path(document, %w[http request referrer], request["referrer"])
  put_path(document, %w[http response status_code], integer(dig_hash(original, "http_response", "code")))
  put_path(document, %w[url full], url["url_string"] || url["full"])
  put_path(document, %w[url scheme], url["scheme"])
  put_path(document, %w[url domain], url["hostname"] || url["domain"])
  put_path(document, %w[url port], integer(url["port"]))
  put_path(document, %w[url path], url["path"])
  put_path(document, %w[url query], url["query_string"] || url["query"])
end

def promote_host_file_user(document, original)
  device = dig_hash(original, "device") || dig_hash(original, "host") || {}
  put_path(document, %w[host hostname], device["hostname"])
  put_path(document, %w[host name], device["name"] || device["hostname"])
  put_path(document, %w[host os name], first_value(device, ["os", "name"], ["os_name"]))
  put_path(document, %w[host os version], first_value(device, ["os", "version"], ["os_version"]))
  user = dig_hash(original, "actor", "user") || dig_hash(original, "user") || {}
  put_path(document, %w[user id], user["uid"] || user["id"])
  put_path(document, %w[user name], user["name"])
  file = dig_hash(original, "file") || dig_hash(original, "file_info") || {}
  put_path(document, %w[file path], file["path"])
  put_path(document, %w[file name], file["name"])
  put_path(document, %w[file directory], file["directory"])
  put_path(document, %w[file extension], file["extension"])
  put_path(document, %w[file size], integer(file["size"]))
  hashes = file["hashes"]
  document["file"]["hash"] = hashes if document["file"].is_a?(Hash) && hashes.is_a?(Hash)
end

def promote_container_rule_dns(document, original)
  container = dig_hash(original, "container") || {}
  put_path(document, %w[container id], container["uid"] || container["id"])
  put_path(document, %w[container name], container["name"])
  put_path(document, %w[container image name], first_value(container, ["image", "name"], ["image_name"]))
  rule = dig_hash(original, "firewall_rule") || dig_hash(original, "rule") || {}
  put_path(document, %w[rule id], rule["uid"] || rule["id"])
  put_path(document, %w[rule name], rule["name"])
  put_path(document, %w[rule category], rule["type"] || rule["category"])
  put_path(document, %w[rule ruleset], rule["ruleset"])
  dns = dig_hash(original, "dns") || dig_hash(original, "dns_query") || {}
  put_path(document, %w[dns question name], first_value(dns, ["question", "name"], ["hostname"], ["query"]))
end

def to_ecs(event, raw, received)
  data = event["data"]
  openshell = data["openshell"].is_a?(Hash) ? data["openshell"] : {}
  security = data["security"].is_a?(Hash) ? data["security"] : {}
  ocsf = security["ocsf"].is_a?(Hash) ? security["ocsf"] : {}
  validation = security["validation"].is_a?(Hash) ? security["validation"] : {}
  redaction = security["redaction"].is_a?(Hash) ? security["redaction"] : {}
  acquisition = data["acquisition"].is_a?(Hash) ? data["acquisition"] : {}
  correlation = data["correlation"].is_a?(Hash) ? data["correlation"] : {}
  original = data["original"].is_a?(Hash) ? data["original"] : { "value" => data["original"] }
  observed = data["observed_time"]
  timestamp = event["time"] || observed || received
  policy_chunk_id = correlation["policy_chunk_id"] || first_value(original, ["policy_chunk_id"], ["policy", "chunk_id"], ["unmapped", "chunk_id"])
  source_status = dig_hash(original, "source_payload", "status") || {}
  original_sandbox = original["sandbox"].is_a?(Hash) ? original["sandbox"] : {}
  phase = original_sandbox["phase"] || source_status["phase"]
  resource_version = original_sandbox["resource_version"] || dig_hash(original, "source_payload", "metadata", "resource_version")
  message = original["message"] || original["status_detail"] || "OpenShell security evidence: #{event['type']}"
  outcome, event_types, tags = classify(event["type"], message.to_s, ocsf, validation)
  document = {
    "@timestamp" => timestamp,
    "message" => message.to_s,
    "tags" => tags,
    "event" => {
      "id" => event["id"], "kind" => "event", "category" => event_categories(event["type"], ocsf),
      "type" => event_types, "action" => event["type"], "outcome" => outcome,
      "dataset" => "openshell.security", "module" => "openshell", "provider" => "NVIDIA OpenShell",
      "original" => raw, "ingested" => received, "reason" => original["status_detail"] || original["reason"]
    },
    "observer" => { "name" => openshell["gateway_id"], "vendor" => "NVIDIA", "product" => "OpenShell", "version" => openshell["gateway_version"] },
    "service" => { "name" => "openshell-gateway" },
    "openshell" => {
      "cloud_event" => {
        "id" => event["id"], "source" => event["source"], "type" => event["type"], "subject" => event["subject"],
        "specversion" => event["specversion"], "dataschema" => event["dataschema"], "extensions" => extensions(event)
      },
      "gateway" => { "id" => openshell["gateway_id"], "version" => openshell["gateway_version"] },
      "workspace" => openshell["workspace"],
      "sandbox" => {
        "id" => openshell["sandbox_id"], "name" => openshell["sandbox_name"], "phase" => phase,
        "resource_version" => resource_version, "lifecycle_event" => original["event_type"],
        "state_observed_at" => observed, "status" => source_status
      },
      "policy" => { "version" => openshell["policy_version"], "revision" => openshell["policy_revision"] },
      "acquisition" => acquisition, "ocsf" => ocsf, "validation" => validation,
      "redaction" => redaction, "correlation" => correlation, "source" => original,
      "request" => { "id" => correlation["request_id"] },
      "tool_call" => { "id" => correlation["tool_call_id"] },
      "policy_chunk" => { "id" => policy_chunk_id }
    }
  }
  put_path(document, %w[trace id], correlation["trace_id"])
  put_path(document, %w[span id], correlation["span_id"])
  put_path(document, %w[transaction id], correlation["request_id"])
  put_path(document, %w[session id], correlation["session_id"] || correlation["agent.session.id"])
  promote_process(document, original)
  promote_network(document, original)
  promote_http(document, original)
  promote_host_file_user(document, original)
  promote_container_rule_dns(document, original)
  document
end

def diagnostic(event, reason, received)
  event.set("@timestamp", LogStash::Timestamp.new(Time.iso8601(received)))
  event.set("message", "OpenShell CloudEvents batch rejected after durable Logstash intake")
  event.set("event", {
              "kind" => "pipeline_error", "category" => ["configuration"], "type" => ["error"],
              "action" => "openshell.cloudevents.batch_rejected", "outcome" => "failure", "reason" => reason
            })
  event.set("tags", ["openshell", "cloudevents", "intake-diagnostic"])
  event.set("[@metadata][openshell_route]", "diagnostic")
  event.remove("headers")
  event
end

def filter(event)
  received = Time.now.utc.iso8601(9)
  content_type = request_content_type(event)
  unless normalized_media_type(content_type) == EXPECTED_CONTENT_TYPE
    return [diagnostic(event, "Content-Type must be #{EXPECTED_CONTENT_TYPE}", received)]
  end

  raw = event.get("message")
  return [diagnostic(event, "request body is missing", received)] unless raw.is_a?(String)
  return [diagnostic(event, "request exceeds 4 MiB", received)] if raw.bytesize > MAX_REQUEST_BYTES

  begin
    batch = JSON.parse(raw)
  rescue JSON::ParserError
    return [diagnostic(event, "request is not valid JSON", received)]
  end
  return [diagnostic(event, "body must be a CloudEvents JSON batch", received)] unless batch.is_a?(Array)
  return [diagnostic(event, "batch must contain 1 to 500 events", received)] if batch.empty? || batch.length > MAX_EVENTS

  encoded = []
  batch.each_with_index do |cloud_event, index|
    candidate = JSON.generate(cloud_event)
    return [diagnostic(event, "event #{index} exceeds 1 MiB", received)] if candidate.bytesize > MAX_EVENT_BYTES
    error = validate_event(cloud_event)
    return [diagnostic(event, "event #{index}: #{error}", received)] unless error.nil?
    encoded << candidate
  end

  batch.each_with_index.map do |cloud_event, index|
    output = LogStash::Event.new(to_ecs(cloud_event, encoded[index], received))
    identity = Digest::SHA256.hexdigest("#{cloud_event['source']}\u0000#{cloud_event['id']}")
    output.set("[@metadata][document_id]", identity)
    output.set("[@metadata][openshell_route]", "security")
    output
  end
end
