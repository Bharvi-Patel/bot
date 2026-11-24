"""Talk to the bot in the terminal (needs .env with SJ_DB_*, RAG_DB_URL and GROQ_API_KEY).

  python chat_cli.py          type a question, press Enter; empty line or Ctrl+C to quit
  python chat_cli.py --debug  also print which tools ran and why a reply was blocked
  python chat_cli.py --customer <customer uuid>   pretend this customer is logged in (tests order lookup without an email)
  python chat_cli.py --store <store uuid>         chat as this store (needed when BOT_STORE_CONFIG_DIR is set in .env)
Guests are identified as "cli" for the order-lookup rate limit. In your web endpoint use the real client IP or session id.
"""
import sys

from bot.context import ChatContext
from bot.llm import get_llm
from bot.router import answer


def main() -> None:
    debug = "--debug" in sys.argv
    customer = sys.argv[sys.argv.index("--customer") + 1] if "--customer" in sys.argv else None
    store = sys.argv[sys.argv.index("--store") + 1] if "--store" in sys.argv else None
    ctx = ChatContext(store_uuid=store, client_id="cli", customer_uuid=customer)
    llm, history = get_llm(), []
    print(f"South Jackson assistant (model: {llm.model}). Empty line to quit.")
    while True:
        try:
            q = input("\nyou> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not q:
            break
        out = answer(q, llm, history, ctx=ctx)
        print("\nbot>", out["reply"])
        if debug:
            print(f"     [tools: {out['tools_used'] or 'none'} | blocked: {out['blocked']}]")
        history += [{"role": "user", "text": q}, {"role": "assistant", "text": out["reply"]}]


if __name__ == "__main__":
    main()