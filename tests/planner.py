import anthropic
from dotenv import load_dotenv
from playwright.async_api import async_playwright
import json

load_dotenv()
client = anthropic.Anthropic()

async def scrape_page(url: str) -> str:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(url)
        html = await page.content()
        await browser.close()
    return html

def extract_testable_elements(html: str, url: str) -> str:
    prompt = f"""You are a QA planner. Analyze this HTML from {url} and identify all testable elements.

The page URL is: {url}
Generate test goals that match the ACTUAL PURPOSE of this specific page. Do NOT default to login testing unless this page is actually a login page. If this is a pricing page, test pricing; if it's a FAQ, test navigation and content; if it's a features page, test CTAs and feature exploration.

Focus on:
- Forms and input fields
- Buttons and links
- Validation rules (required fields, input types, constraints)
- Interactive elements (checkboxes, dropdowns, toggles)

Return a JSON object with exactly this shape:
{{
    "page_summary": "one sentence describing what this page does",
    "testable_elements": [
        {{
            "element": "name or description of the element",
            "type": "form | button | link | input | checkbox | dropdown | other",
            "selector": "best CSS selector for this element",
            "notes": "anything notable about validation or behavior"
        }}
    ],
    "suggested_test_cases": [
        {{
            "goal": "a one sentence test goal written for a QA agent",
            "priority": "high | medium | low",
            "reasoning": "why this test case matters"
        }}
    ]
}}

HTML:
{html[:15000]}"""

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = response.content[0].text
    clean = raw.replace("```json", "").replace("```", "").strip()
    return json.loads(clean)

async def plan(url: str) -> dict:
    print(f"🔍 Scraping {url}...")
    html = await scrape_page(url)
    
    print("🧠 Analyzing page structure...")
    plan = extract_testable_elements(html, url)
    
    print(f"\n📋 Page: {plan['page_summary']}")
    print(f"   Found {len(plan['testable_elements'])} testable elements")
    print(f"   Generated {len(plan['suggested_test_cases'])} test cases\n")
    
    for i, tc in enumerate(plan['suggested_test_cases']):
        priority_icon = "🔴" if tc['priority'] == "high" else "🟡" if tc['priority'] == "medium" else "🟢"
        print(f"   {priority_icon} [{tc['priority'].upper()}] {tc['goal']}")
    
    return plan

if __name__ == "__main__":
    import asyncio
    url = "https://the-internet.herokuapp.com/login"
    result = asyncio.run(plan(url))