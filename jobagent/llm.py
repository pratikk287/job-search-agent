"""Optional Claude drafting. Every caller has a template fallback, and every output is fact-checked
against the source resume before it is used."""
import re

from . import config

SYSTEM = (
    "You draft job-application writing for one candidate. Use only facts present in the RESUME and CANDIDATE "
    "sections. Never invent employers, titles, dates, metrics, tools, certifications or results; if a JD asks for "
    "something the resume lacks, do not claim it. Write in plain, specific, professional Indian-English business "
    "style. No clichés ('I am writing to express', 'passionate', 'synergy'), no em dashes, no emojis. "
    "Return only the requested text, with no preamble."
)


def available():
    s = config.settings().get("llm", {})
    return bool(s.get("enabled", True)) and config.integrations()["claude"]


def generate(prompt, max_tokens=4000):
    """Returns text, or None if Claude is unavailable, refused, or errored."""
    if not available():
        return None
    try:
        import anthropic
    except ImportError:
        return None
    s = config.settings().get("llm", {})
    client = anthropic.Anthropic(api_key=config.secret("ANTHROPIC_API_KEY"), base_url="https://api.anthropic.com")
    try:
        resp = client.beta.messages.create(
            model=s.get("model", "claude-opus-5-5"),
            max_tokens=max_tokens,
            system=SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            output_config={"effort": s.get("effort", "medium")},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.BadRequestError:
        # e.g. a model/account that does not accept the fallback beta: retry without it
        try:
            resp = client.messages.create(model=s.get("model", "claude-opus-5-5"), max_tokens=max_tokens,
                                          system=SYSTEM, messages=[{"role": "user", "content": prompt}])
        except anthropic.APIError:
            return None
    except (anthropic.APIConnectionError, anthropic.RateLimitError, anthropic.APIStatusError):
        return None
    if resp.stop_reason == "refusal":
        return None
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    return text or None


def invented_numbers(text, allowed):
    """Numbers in generated text that do not occur in the resume or JD (likely fabricated metrics)."""
    found = set(re.findall(r"\d+(?:\.\d+)?", text or ""))
    return sorted(n for n in found if n not in allowed and not (len(n) == 4 and n.startswith(("19", "20"))))
