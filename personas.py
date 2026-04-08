import json
from anthropic import Anthropic

client = Anthropic()

DEFAULT_PERSONAS = [
    {
        "name": "First-Time Visitor",
        "description": "Someone arriving at the site for the first time with no prior knowledge of the brand, product, or service.",
        "goals": [
            "Quickly understand what the site offers",
            "Determine if it's relevant to their needs",
            "Find a clear next step without confusion",
        ],
        "concerns": [
            "Unclear value proposition above the fold",
            "Too much jargon or assumed prior knowledge",
            "No obvious path forward after landing",
            "Slow load times or visual clutter",
        ],
    },
    {
        "name": "Mobile User",
        "description": "A user on a smartphone, likely in a hurry or a distracting environment, navigating with one thumb.",
        "goals": [
            "Complete their task quickly with minimal tapping",
            "Find contact info or key actions without excessive scrolling",
            "Navigate without pinching or horizontal scrolling",
        ],
        "concerns": [
            "Small tap targets or dense layouts",
            "Forms that are difficult to complete on a small screen",
            "Content or features that require desktop to use fully",
            "Pop-ups or overlays that are hard to dismiss",
        ],
    },
    {
        "name": "Skeptical Buyer",
        "description": "A cautious prospect who has been burned before and needs significant trust signals before taking any action.",
        "goals": [
            "Find proof the product or service delivers on its promises",
            "Understand pricing and terms clearly before committing",
            "See social proof: reviews, case studies, or credentials",
        ],
        "concerns": [
            "Vague or over-hyped copy with no specifics",
            "Hidden pricing or forced sign-up to see details",
            "No visible testimonials, reviews, or third-party validation",
            "Unclear refund, cancellation, or data privacy policy",
        ],
    },
    {
        "name": "Accessibility-Focused User",
        "description": "A user who relies on accessible design — sufficient color contrast, readable fonts, keyboard navigation, and clear labels.",
        "goals": [
            "Read and understand all content without visual strain",
            "Navigate the full site without relying solely on a mouse",
            "Complete forms with clearly labeled, well-spaced fields",
        ],
        "concerns": [
            "Low color contrast between text and background",
            "Icon-only buttons or images without descriptive alt text",
            "No visible focus states on interactive elements",
            "Auto-playing media or animations that cannot be paused",
        ],
    },
]


async def generate_personas(url: str, report_summary: str) -> list:
    """
    Call Claude to generate 3-5 contextually appropriate personas for a site.
    Falls back to DEFAULT_PERSONAS on any failure.
    """
    prompt = f"""You are a UX research expert. Given the following website URL and a brief summary of a UX evaluation, generate 3-5 contextually appropriate user personas for this specific site.

URL: {url}

UX Evaluation Summary:
{report_summary}

Return ONLY a JSON array of persona objects. Each object must have exactly these fields:
- name: string (short persona label, e.g. "Prospective Homebuyer")
- description: string (1-2 sentence description of this person)
- goals: array of 3-4 strings (what they want to accomplish on this site)
- concerns: array of 3-4 strings (what friction or doubts they might have)

Return nothing but the JSON array. No markdown, no explanation."""

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        clean = raw.replace("```json", "").replace("```", "").strip()
        personas = json.loads(clean)
        if not isinstance(personas, list) or not personas:
            raise ValueError("Expected non-empty JSON array")
        return personas
    except Exception as e:
        print(f"⚠️  Persona generation failed: {e} — using default personas")
        return DEFAULT_PERSONAS
