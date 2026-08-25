# Token inspection endpoint

`GET /v1/tokens/{token_id}` returns metadata for one token. It never returns the
token secret.

## Request

Pass the token identifier as the URL path segment `token_id`. Send an operator
credential in `Authorization: Bearer <credential>`.

```http
GET /v1/tokens/tok_123
Authorization: Bearer <credential>
```

## Responses

- `200 OK` returns `id`, `created_at`, `expires_at`, and `status`.
- `401 Unauthorized` means the credential is missing or invalid.
- `403 Forbidden` means the credential cannot inspect this token.
- `404 Not Found` means the token identifier does not exist.

Timestamps use RFC 3339 UTC strings. `status` is `active`, `expired`, or
`revoked`. Clients must treat unknown future status values as unavailable.
