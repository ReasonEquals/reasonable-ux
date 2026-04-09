import json

from anthropic import Anthropic
from dotenv import load_dotenv

DEFAULT_PERSONAS = [
    {
        "name": "Evaluator",
        "description": "A decision-maker comparing this product against alternatives during a free trial or demo, weighing whether it solves their team's problem before committing budget.",
        "goals": [
            "Quickly understand what the product does and who it's for",
            "Find pricing, plan limits, and contract terms without sales friction",
            "See proof the product works: case studies, customer logos, reviews",
            "Determine how it compares to competing tools they're evaluating",
            "Identify a clear path to start a trial or book a demo",
        ],
        "concerns": [
            "Vague marketing copy with no concrete capabilities",
            "Hidden pricing or forced sales conversations to see basic details",
            "Lack of social proof or recognizable customers",
            "Unclear differentiation from competitors",
            "No obvious next step for someone ready to try it",
        ],
    },
    {
        "name": "Hands-on End User",
        "description": "Someone who will use the product day-to-day to get their job done, often onboarded by an admin and learning the interface as they go.",
        "goals": [
            "Complete core tasks without hunting through menus or docs",
            "Learn the interface quickly through obvious affordances",
            "Find help or documentation when stuck",
            "Customize the experience to fit their workflow",
        ],
        "concerns": [
            "Cluttered or unintuitive navigation",
            "Jargon and feature names that don't map to their mental model",
            "Slow page loads or laggy interactions interrupting flow",
            "Missing keyboard shortcuts or bulk actions for repetitive work",
            "Help content that's hard to find or out of date",
        ],
    },
    {
        "name": "Technical Integrator",
        "description": "A developer or IT admin assessing whether the product can be integrated into their existing stack — evaluating APIs, SSO, data export, and security posture.",
        "goals": [
            "Find API documentation and authentication details",
            "Verify SSO, SCIM, and role-based access support",
            "Understand data residency, compliance, and security certifications",
            "Estimate integration effort before committing",
        ],
        "concerns": [
            "API docs that are missing, incomplete, or hidden behind sign-up",
            "No clear answer on SOC 2, GDPR, or other compliance requirements",
            "Lack of webhooks, export, or programmatic access to data",
            "Unclear rate limits or undocumented breaking changes",
            "Vendor lock-in with no migration path",
        ],
    },
]


async def generate_personas(url, summary):
    load_dotenv()
    client = Anthropic()

    system_prompt = (
        "You generate realistic user personas for UX research. "
        "Return only valid JSON arrays. No markdown, no explanation."
    )

    user_prompt = f"""URL: {url}

UX report summary:
{summary}

Infer the product type and the target audience from the URL and report summary above. Then return a JSON array of exactly 3 personas that reflect the actual likely users of this specific product — not generic archetypes.

Each persona object must have exactly these fields:
- name: string (short persona label)
- description: string (1-2 sentences describing this person)
- goals: array of 3-5 strings (what they want to accomplish on this site)
- concerns: array of 3-5 strings (friction, doubts, or risks they perceive)

Return only the JSON array, nothing else."""

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1500,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        raw = response.content[0].text.strip()
        clean = raw.replace("```json", "").replace("```", "").strip()
        return json.loads(clean)
    except Exception as e:
        print(f"⚠️  Persona generation failed: {e} — using DEFAULT_PERSONAS fallback")
        return DEFAULT_PERSONAS
