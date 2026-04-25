# Terms of Use — reasonable-ux

## Third-party website compliance

You are responsible for ensuring your use of this tool complies with the Terms of Service, robots.txt directives, and applicable law of any website you target. The tool drives a real Chromium browser via Playwright and behaves like a human user — it does not automatically respect robots.txt or rate limits. Authorization to audit a site is your responsibility, not the tool's.

## Data handling

All run artifacts — screenshots, JSON reports, PDF files — are stored locally under `runs/` on your machine. Nothing is persisted by this project. The only data transmitted to third parties is the standard API call payload (screenshot images and page text) sent to your chosen LLM provider (Anthropic, OpenAI, or Google) for analysis. Langfuse observability tracing is opt-in and gated on the `LANGFUSE_PUBLIC_KEY` environment variable.

## API usage and costs

You are responsible for your own API costs and for complying with the Terms of Service of any LLM provider you use (Anthropic, OpenAI, Google). API credentials are yours; do not commit them to version control.

## Acceptable use

Do not use this tool to:
- Crawl or scrape sites at scale without authorization
- Circumvent access controls, CAPTCHAs, or authentication walls you are not authorized to bypass
- Target sites you do not own or have explicit permission to audit

## No warranty

This software is provided as-is under the [MIT License](LICENSE). No warranty is expressed or implied. The authors are not liable for any damages arising from its use.
