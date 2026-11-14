"""Builds the exact words the voice will say. Deterministic: only facts the wearer
selected on the glasses plus profile text the wearer wrote in advance. Nothing is
inferred from silence and no AI decides what is said."""
from xml.sax.saxutils import escape

# key -> (button label, spoken sentence)
FACTS = {
    "cannot_speak": ("Cannot speak", "They cannot speak right now, so this message is synthesized."),
    "medical":      ("Medical help", "They selected that they need medical help."),
    "unsafe":       ("Feel unsafe", "They selected that they feel unsafe."),
    "fallen":       ("Fallen / hurt", "They selected that they have fallen or are hurt."),
}
FACT_ORDER = ["cannot_speak", "medical", "unsafe", "fallen"]


def build_script(*, kind, name, facts, location, share_location, description="",
                 callback="", recipient_label=""):
    """kind: 'emergency' (demo line) or 'contact'."""
    who = name or "the wearer"
    if kind == "emergency":
        lines = [f"This is an automated emergency assistance call from Raven Reach on behalf of {who}."]
    else:
        greeting = f"Hello {recipient_label}. " if recipient_label else ""
        lines = [f"{greeting}This is an automated call from Raven Reach on behalf of {who}, who asked to reach you."]
    for key in FACT_ORDER:
        if key in facts:
            lines.append(FACTS[key][1])
    if share_location:
        lines.append(location.spoken() if location else "Location unavailable.")
    else:
        lines.append("Location was not shared.")
    if description.strip():
        lines.append(f"Information they provided in advance: {description.strip().rstrip('.')}.")
    if callback:
        spaced = " ".join(callback.lstrip("+"))
        lines.append(f"Callback number: plus {spaced}.")
    return " ".join(lines)


def build_twiml(script, voice="Polly.Joanna-Neural", repeats=2, language="en-US"):
    """TwiML spoken when the callee answers. Repeats so a listener can catch details."""
    say = f'<Say voice="{escape(voice)}" language="{escape(language)}">{escape(script)}</Say>'
    body = ['<Pause length="1"/>', say]
    for _ in range(max(0, int(repeats) - 1)):
        body += ['<Pause length="2"/>',
                 f'<Say voice="{escape(voice)}" language="{escape(language)}">Repeating.</Say>', say]
    body.append(f'<Say voice="{escape(voice)}" language="{escape(language)}">End of message. Goodbye.</Say>')
    return "<Response>" + "".join(body) + "</Response>"
