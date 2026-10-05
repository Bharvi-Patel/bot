import sys, time
from dotenv import load_dotenv; load_dotenv()
from sjbot import guardrails as g, router
from sjbot.llm import get_llm

llm = get_llm()
orig = llm.generate
def gen(m):
    t = time.perf_counter(); r = orig(m)
    print(f"  LLM call {time.perf_counter()-t:.2f}s -> {[c.name for c in r.tool_calls] or 'final answer'}")
    return r
llm.generate = gen

for name, fn in list(g.TOOLS.items()):
    def wrap(a, _fn=fn, _n=name):
        t = time.perf_counter(); r = _fn(a)
        print(f"  tool {_n} {time.perf_counter()-t:.2f}s")
        return r
    g.TOOLS[name] = wrap

q = " ".join(sys.argv[1:]) or "Show me black loveseats under $800"
for i in (1, 2):                      # run 1 = cold, run 2 = warm
    print(f"run {i}")
    t = time.perf_counter(); router.answer(q, llm)
    print(f"  TOTAL {time.perf_counter()-t:.2f}s")