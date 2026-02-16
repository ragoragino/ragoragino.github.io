---
layout: post
title:  "AI Security"
date:   2026-02-16 00:00:00 +0100
categories: SoftwareEngineering Miscellaneous
---

### Lethal Trifecta

The most important conceptual framework for tying together different risks in the AI space is the so-called _lethal trifecta_, where LLMs effectively act as a confused deputy. The trifecta applies when three prerequisites are met: the LLM has access to confidential or sensitive data, has a way of communicating with actors outside the security boundary (e.g., a company’s virtual network), and the agent accepts arbitrary input into the prompt used in its decision loop ([source](https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/)).

### MCP

**Terminology.** For the rest of this section, it helps to have a few terms straight. The **MCP resource server** (in OAuth/RFC terms) is the MCP server that serves tools, resources, and prompts—it is the “API” that clients talk to. It advertises how to find and use the authorization layer via **Protected Resource Metadata**, which tells the client where the **authorization server** lives and what scopes it supports. The authorization server is the component that issues tokens after authenticating the user (and possibly the client). In short: the MCP resource server is the thing you protect; the authorization server is the thing that grants access to it.

There are two core perspectives on MCP servers: MCP servers that you expose internally or externally, and MCP servers (hosted internally or externally) that your agents consume.

The security concerns of the former do not differ significantly from those of building traditional APIs. This includes authentication and authorization of clients calling the API, protection against DoS via rate limiting, and input sanitization, to name a few. In the traditional API world, clients calling APIs are most often pre-registered. You would typically use pre-created API keys or OAuth Client Credentials. Even when layering OAuth Authorization Code Flow to enable user-level access patterns, we are still talking about pre-created clients accessing the API on behalf of users.

With MCP servers, however, the expectation is that clients are far more numerous and heterogeneous. These might include Cursor or Claude Code instances on developers’ machines, customer support agents, or Slack bots. In these cases, creating and maintaining separate API keys for all instances becomes an inherent security risk due to key proliferation.

The core problem, therefore, is how to grant access to an MCP server to a multitude of different clients. The MCP Committee went through several iterations to solve this client registration problem. It initially supported a simple pre-registered flow in which clients send a predefined client ID (and potentially a client secret) that the authorization server recognizes. This is essentially the straightforward—but cumbersome—API key per client approach. Later, a **dynamic client registration flow** was added to the standard, but a few months after its introduction it was deprecated in favor of the **OAuth Client ID Metadata Document (CIMD)** flow. The reason for the deprecation was that dynamic registration did not contain provisions for reliable client identification. The MCP standard therefore moved forward and made the OAuth Client ID Metadata Document flow the default for new clients. In this flow, the client ID is a URL pointing to a JSON metadata document containing additional details about the client. The authorization server can verify that the client ID matches the one in its metadata document (with additional redirect URL validation). Authorization servers for MCP resources can thus maintain a simple allowlist of client URLs (e.g., the Claude Code URL at Anthropic’s domain).

After client registration, the remainder of the authorization process follows the standard OAuth 2.1 Authorization Code Flow with PKCE ([source](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization)).

Even though the full **CIMD + OAuth 2.1 PKCE** flow is now part of the MCP standard, the majority of enterprise IdP providers (and even model providers) do not support it yet. As a result, the ecosystem has rapidly turned to the oldest tool in the programmer’s toolbox: indirection. OAuthProxy is an authorization server that runs alongside your MCP server and speaks the MCP authorization standard on one side while translating it to different IdP requirements on the other. This allows enterprises to support compliant MCP clients while continuing to use existing IdP solutions (like Github, Entra, Google, etc.) ([see e.g. the FastMCP implementation of the proxy](https://github.com/jlowin/fastmcp/pull/2871)). The downside is that you are now operating an additional authorization server in your infrastructure, which introduces a separate attack surface—particularly given how recent and rapidly evolving many MCP frameworks still are.

Even though the MCP standard has resolved the client registration problem to a reasonable degree, the pain points do not end there. One major open question is how to manage the proliferation of MCP tools. If every MCP tool requires user consent via OAuth Authorization Code Flow, this can quickly lead to consent fatigue, opening up a new set of security issues. A proposed protocol called **Cross-App Access (XSS)**, introduced by Okta, aims to eliminate this user burden. Its core idea is to position the identity provider (IdP) as the source of cross-app trust, allowing authorization servers protecting MCP resources (e.g., Slack, Salesforce) to accept certain JWTs for authorization code exchange if they are signed by a trusted IdP. This proposal is still in draft form within the IETF standardization process ([source](https://oauth.net/cross-app-access/)).

The similarity between MCP server security and traditional API security breaks down when considering the opposite perspective: agents consuming MCP servers. Because agents may use outputs from MCP servers (prompts, resources, or tool outputs) as part of their reasoning, the security model shifts toward input sanitization and content trust. Consuming MCP outputs effectively enables the third leg of the lethal trifecta: arbitrary input can influence the LLM’s subsequent reasoning and actions.

These risks are not merely theoretical. Two patterns observed in practice are tool description poisoning—malicious instructions embedded in tool descriptions, help text, or parameter documentation, sometimes using invisible Unicode or hidden prompts—and schema manipulation, where tool or schema definitions are tampered with so that an operation that appears safe (e.g., “archive”) maps to a dangerous action (e.g., delete). Validation may still pass, and audits can appear correct. In some cases, poisoned tool descriptions can steer models to misuse other legitimate tools (so-called implicit poisoning), with high success rates reported in studies.

Mitigating these threats is non-trivial and typically requires a combination of deterministic and LLM-based mechanisms. By versioning and cryptographically signing MCP server schemas, organizations can pre-approve MCP servers before use. Additionally, a policy enforcement layer in front of MCP tools can evaluate intent and risk before execution. This includes classifying tool calls (read vs. write vs. destructive), enforcing allow/deny policies based on data sensitivity and agent identity, rate-limiting high-risk actions, requiring step-up approval or human-in-the-loop for irreversible operations, and bounding blast radius with per-agent quotas and kill switches.

### Agent Identity

The previous discussion of MCP server authentication and authorization largely assumed that a human directly initiates the connection. Users grant agents permissions via OAuth consent to access their data through MCP servers. In practice, however, this model is overly simplistic. Real-world deployments involve much more complex environments in which agents communicate with one another, delegate tasks, and call APIs and MCP servers in arbitrary sequences. These agents may also require specific user scopes to access different APIs or MCP servers.

In traditional architectures, OAuth Client Credentials flows are used for simple machine-to-machine authentication, while OAuth Authorization Code Flow is used for human-delegated access. For multi-hop request chains, OAuth Token Exchange (the so-called On-Behalf-Of flow) is used to update token claims—most commonly `aud` and `scope`—as requests traverse multiple services.

Token exchange is likely to be particularly useful in agentic systems. In addition to re-scoping tokens for new audiences and scopes, it may be desirable to preserve a trace of the agent actors involved in downstream calls. This can be achieved by appending additional claims to JWTs to represent the chain of agentic decisions.

Several approaches to assigning and validating agent identities are emerging. Microsoft’s Entra Agent ID builds on app registration and service principal flows but avoids static client credentials and secrets. It separates an “agent blueprint” (a higher-level manifest) from individual agent instances. Each instantiated agent receives a unique derived identity and OAuth token for accessing external systems, significantly improving auditability and permission scoping ([source](https://blog.christianposta.com/entra-agent-id-agw/PART-1.html)). 

The open-source, cloud-native project Kagenti follows a similar pattern, separating an orchestrator from individual agents. It uses SPIFFE to assign identities to agent workloads and OAuth Token Exchange to narrow access tokens for inter-agent communication ([source](https://github.com/kagenti/kagenti/blob/main/docs/components.md#identity--auth-bridge)).

Although enterprise environments will vary significantly in how these approaches can be applied in practice, the architectural patterns explored by these projects provide useful guidance for adapting authentication and authorization frameworks to the agentic era.

### Conclusion

In addition to the concerns discussed above, the proliferation of external LLM tools and their integration with internal data systems is creating new vectors for enterprise data privacy and security risk. Policies such as zero data retention, no training on customer data, comprehensive audit logging, and strict isolation between external tools (e.g., custom GPTs or external MCP servers) are non-negotiable for safely navigating this landscape.

The sheer speed at which AI startups are driving the adoption of generative AI has resulted in a highly fragmented ecosystem. We see cutting-edge agentic workflows alongside early drafts of standards intended to address the security risks inherent to them, all while the majority of market providers lag in supporting these standards even in beta or preview form. This split reality is unlikely to change soon given the pace of the ecosystem, and security practitioners and developers will likely need to accept living with a certain level of paranoia around the systems they build and protect.

### Additional sources:

- https://openid.net/wp-content/uploads/2025/10/Identity-Management-for-Agentic-AI.pdf
- https://blog.christianposta.com/explaining-on-behalf-of-for-ai-agents/
- https://openreview.net/pdf/7db8d7d31396bd9a8cc21dbbc479c7511639f8d8.pdf
- https://owasp.org/www-project-top-10-for-large-language-model-applications