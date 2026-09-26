# Production mapping

| Concern | PoC | Azure production | AWS production |
|---|---|---|---|
| UI hosting | nginx container, strict CSP | Static Web Apps / Front Door + WAF | CloudFront + S3 + WAF |
| BFF + agent runtime | FastAPI + LangGraph container | Container Apps / AKS; or Foundry Agent Service for hosted agents | ECS/EKS; or Bedrock AgentCore Runtime |
| Conversation memory | `InMemorySaver` | LangGraph `PostgresSaver` on Azure Database for PostgreSQL | `PostgresSaver` on Aurora / RDS |
| User identity | Mock IdP persona login (stub 1) | Entra ID, OIDC + PKCE via BFF | Cognito or enterprise IdP |
| Agent identity + delegation | Mock RFC 8693 token exchange, per-audience tokens | **Entra Agent ID** + on-behalf-of | **AgentCore Identity** |
| Policy decision | `policy.authorize()` in each skill (stub 2) | OPA / Cedar sidecar, policies in their own repo | Amazon Verified Permissions (Cedar) |
| Skills (MCP servers) | FastMCP over streamable HTTP, one per data product | Container Apps behind **APIM MCP gateway** | ECS/Lambda behind **AgentCore Gateway** |
| Model gateway | LiteLLM (`gateway/profiles`) | **APIM AI Gateway**: backend pools, circuit breaker, token limits, semantic cache, managed identity | **Bedrock** (Converse, cross-region inference, Guardrails); LiteLLM on EKS for non-Bedrock vendors |
| Managed router (optional, behind alias) | – | Foundry Model Router | Bedrock Intelligent Prompt Routing |
| Models | Scripted fake / gpt-5-mini + gpt-5.4 | Azure OpenAI / Foundry deployments | Bedrock models |
| Secrets | `.env` (gitignored, generated) | Key Vault + managed identity (no keys in pods) | Secrets Manager + IAM roles |
| Audit / lineage | `audit.events` table (append-only via grants) | OTel GenAI spans → App Insights, plus an immutable audit store (Log Analytics / immutable Blob) | OTel → CloudWatch / X-Ray, plus S3 Object Lock |
| Evals | `evals/` harness, on demand | Same harness in the release pipeline; Foundry evaluations for advisory quality scores | Same harness; Bedrock Evaluations for advisory scores |
| Promotion registry | `registry.assets` table | Registry service / API Center metadata; deployment gate reads the certificate | Same pattern (e.g. a DynamoDB-backed registry) |
| Data products | Postgres schemas + one role per skill | Per-domain databases, private endpoints | Per-domain databases, VPC endpoints |

## What would change first when moving to production

1. **Stub 1 → Entra Agent ID + OBO.** The skills only change the JWKS URL and issuer.
2. **LiteLLM → APIM AI Gateway.** Aliases become APIM API operations or backends; the agent's base URL changes. This is the same seam, and ADR-001 covers it.
3. **The eval harness runs in the release pipeline.** The deployment step refuses a composition whose certificate is not `certified` and current.
4. **Checkpointer → `PostgresSaver`.** Per-service images, with signing and SBOM.
