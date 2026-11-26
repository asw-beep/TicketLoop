"""The checkout page: plain HTML, no script, so it works in any browser."""

from html import escape

from fakepay.store import Intent, Status

OUTCOMES = {
    "succeed": ("Pay", "The payment succeeds and one webhook is sent."),
    "fail": ("Decline", "The payment fails and one webhook says so."),
    "delay": ("Pay, webhook late", "Succeeds now; the webhook arrives seconds later."),
    "twice": ("Pay, webhook twice", "Succeeds; the same webhook is delivered two times."),
    "bad_signature": ("Pay, forged webhook", "Succeeds; the webhook's signature is wrong."),
    "no_webhook": ("Pay, no webhook", "Succeeds; nothing is sent. Reconciliation must notice."),
}

SETTLED: dict[Status, str] = {
    "succeeded": "This payment went through.",
    "failed": "This payment was declined.",
    "expired": "This payment page has expired. Nothing was charged.",
}

STYLE = """
:root { color-scheme: light dark; }
body { font: 16px/1.5 system-ui, sans-serif; max-width: 34rem; margin: 3rem auto; padding: 0 1rem;
  color: light-dark(#1c2a22, #e6efe9); background: light-dark(#f2f6f3, #101713); }
h1 { font-size: 1.1rem; letter-spacing: .02em; margin: 0; }
.amount { font-size: 2.6rem; font-weight: 700; margin: .6rem 0 0; }
.meta { color: light-dark(#51615a, #9fb0a7); font-size: .9rem; overflow-wrap: anywhere; }
form { display: grid; gap: .7rem; margin-top: 1.6rem; }
button { font: inherit; text-align: left; padding: .8rem 1rem; border-radius: 10px; cursor: pointer;
  border: 1px solid light-dark(#c3d0c8, #2f3d36); color: inherit;
  background: light-dark(#fff, #18211c); }
button:focus-visible, a:focus-visible { outline: 3px solid light-dark(#1d6b45, #86d9ab); }
button strong { display: block; }
button:first-child { background: #1d6b45; border-color: #1d6b45; color: #fff; }
button:first-child .meta { color: #d7ebe0; }
a { color: light-dark(#1d6b45, #86d9ab); }
"""


def checkout_page(intent: Intent, status: Status) -> str:
    if status == "open":
        buttons = "".join(
            f'<button name="outcome" value="{value}"><strong>{escape(label)}</strong>'
            f'<span class="meta">{escape(note)}</span></button>'
            for value, (label, note) in OUTCOMES.items()
        )
        target = f"/checkout/{escape(intent.transaction_id)}"
        action = f'<form method="post" action="{target}">{buttons}</form>'
    else:
        action = (
            f'<p role="status">{SETTLED[status]}</p>'
            f'<p><a href="{escape(intent.return_url)}">Back to TicketLoop</a></p>'
        )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>fakepay checkout</title>
<style>{STYLE}</style>
</head>
<body>
<main>
<h1>fakepay, a pretend payment gateway</h1>
<p class="amount">₹{rupees(intent.amount_paise)}</p>
<p class="meta">No real money moves and no card is asked for.
Transaction {escape(intent.transaction_id)}</p>
{action}
</main>
</body>
</html>"""


def rupees(paise: int) -> str:
    whole, fraction = divmod(paise, 100)
    digits = str(whole)
    if len(digits) > 3:  # Indian grouping: 1,00,000
        head, tail = digits[:-3], digits[-3:]
        pairs = []
        while head:
            pairs.insert(0, head[-2:])
            head = head[:-2]
        digits = ",".join([*pairs, tail])
    return f"{digits}.{fraction:02d}" if fraction else digits
