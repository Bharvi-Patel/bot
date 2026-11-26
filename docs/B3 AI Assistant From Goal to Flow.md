# B3 AI Assistant: From Goal to Flow

*Chat assistant and Ringg.AI voice agent (Roadmap Priority #9). Status as of 26 November 2026, checked against the code on GitHub and the team checklist (docs/gaps\_and\_open\_questions.md). Written in plain language for the team and management.*

## 1. The goal

B3 Cloud Commerce runs many online stores from one shared codebase. The client wants AI to be a **measurable step of the customer journey**, not just another feature. The journey we want to measure is:

Searches → AI interactions → Inquiries → Leads / opportunities → Actions → Transactions / revenue

The work is done in order. Phase 1 is a chat assistant on each store's website. Phase 2 is a voice assistant (Ringg.AI) that talks to callers. A dashboard that shows the numbers comes after both, and it has not been started.

## 2. Where we started and what is different now

**Before:** a chatbot built for one store only, South Jackson Furniture. It read that store's database directly, and its instructions were written for furniture.

**Now:** one shared assistant service that can serve any store on the platform (furniture today; veterinary, electronics, and tires and wheels are on the roadmap).

- It never touches a store's database. It asks the store's API for everything.
- Each store has its own small settings file.
- A store with no settings file, or one switched off, is refused. Nothing falls back to another store's data.

## 3. The whole flow, step by step

1. A shopper types a question on a store's website (or speaks to the voice assistant).
2. The store's website backend passes the question to the assistant service, with trusted details: which store it is, and, if the shopper is logged in, who they are.
3. The assistant loads that store's settings and writes the instructions for its AI model.
4. The AI model decides which tools to use: look up products, categories, brands, store details, shipping text or policies.
5. Each tool asks the store's API for live data, or searches the policy and FAQ pages we saved earlier.
6. Before sending, the assistant checks the draft reply. Prices, phone numbers and links must come from real tool results.
7. The reply goes back to the shopper.
8. A small event is recorded ("a chat happened", "a search happened") with no message text. These events are the raw material for measurement.

**Example:** a shopper asks "What sofas do you have under a thousand dollars?" The assistant looks up the matching category, searches that category with the price limit, and replies with a few real products and their real prices. If the model had invented a price, the check in step 6 would block the reply.

## 4. How the assistant knows which store: the Store-UUID

Every store has a unique ID, the **Store-UUID**. It travels with every request as a header.

- The API looks the ID up in a master list of stores (the "tenants" list). Each entry holds the login for that store's own database.
- The API then switches to that store's database and answers.
- The assistant only passes the ID along. It never sees the database login.

The same ID is the **file name** of the store's settings file. For example, South Jackson's file is named with its long store number.

**Bringing a new store on, in order:**

1. The store must exist in the platform with a tenant entry. According to a code read by another assistant, nothing in the onboarding app creates that entry automatically, so today it would be done by hand. We have not verified this.
2. Create the settings file, named with the store's ID, and start it switched off.
3. Load the store's pages, FAQs, blogs and products into the assistant's knowledge database.
4. Test.
5. Switch it on. This is picked up in about a minute, with no restart.

## 5. The store settings file

One small file per store. It holds only what the store's API cannot tell the assistant.

| Setting | What it does |
| --- | --- |
| store\_name | The name the assistant uses for the store |
| allowed\_domains | The store's website addresses |
| vertical | The kind of store (furniture, electronics, veterinary, tires and wheels, general). It picks the set of rules for that kind |
| enabled | The on/off switch for the assistant in this store only. Off means the assistant is unavailable there, while the store's website keeps working |
| shipping\_example | One sentence used as the example for "we do not ship there" |
| catalog\_rules | A few short rules for oddities in that store's catalog |
| scrub\_terms | Words (such as another showroom's city) whose sentences are removed from page text |

Contact details (phone, email, address, hours) are not in the file. The assistant gets them from the store's API.

## 6. How data is fetched

- **Live store data** (products, prices, categories, brands, store details, inventory): asked from the store's API each time, through tools. The assistant keeps no copy.
- **Policies, FAQs, buying guides and blogs:** copied from the store's pages into a search database. Every search is filtered to that one store.
- **Shipping:** answered from the store's shipping policy text. The API now has a public shipping options endpoint and the assistant can read it, but the shipping tool does not use it yet, so the assistant gives no exact shipping prices.
- **Orders:** only for a logged-in customer. The assistant forwards that customer's own token, and the API returns only their orders. The assistant keeps the order number, status, date, total and item names. Orders are never available by voice, because a caller cannot be identified.
- **Things it does not have:** coupons and promotions (there is no tool for them) and holiday hours. The assistant says it does not have that information instead of guessing.

The voice assistant has ten tools: store info, categories, brands, attribute values, product search, product details, product recommendations, policy search, shipping options and inventory check. The chat adds the order lookup for logged-in customers.

## 7. Safety and privacy built in

- **The reply check.** Every chat reply is checked before it is sent: every price must match a tool result or something the customer typed, phone numbers and emails must be the store's real ones, links must be real, and nothing internal (costs, tool details, the instructions) may leak. A wrong phone number or email is repaired, and a reply with an invented price is blocked.
- **No order changes.** The assistant cannot cancel, change or refund orders, and it says so plainly.
- **No medical or legal claims.**
- **No message text is logged.** The funnel events record what happened, never what was said. A separate review log can store question text only if someone switches that on deliberately, and it is off by default.
- **No database login** is held by the assistant service.
- **Trusted details come from the backend,** never from what a visitor types: the shared service key, the store ID, and the customer's identity.
- **One worker.** The service runs as a single process because its limits are held in memory.

## 8. The chat widget

The widget is the part shoppers see. Both pieces are built in the storefront (the "mw" app):

1. A chat widget on every page of the store's website.
2. A backend route that passes each message to the assistant with the trusted details: the shared service key, the store ID, a client ID, the customer ID and token if logged in, and a conversation ID.

The visitor's browser only ever sends the message and the recent history. The backend decides who is logged in.

## 9. The Ringg.AI voice agent

**Who does what.** Ringg hosts the voice agent: it listens, speaks and manages the phone line or website voice widget. Our assistant supplies the store knowledge.

**The flow of a call:**

1. A caller speaks to the Ringg agent (by phone, or by the website widget).
2. When it needs store facts, the agent calls one of our ten tool addresses. Each request carries the agent's secret key, the agent's ID and the Store-UUID.
3. Our assistant checks the key and the store, asks the store's API, and sends the answer back to be spoken.
4. When the call ends, Ringg sends call events to our webhook. They are recorded in the same event log as chat events.

**One agent, many stores.** The agent learns which store a call is for from two variables, store\_uuid and store\_name, set by the website page or the call. The assistant then checks that this store is allowed for that agent. An agent can be allowed a fixed store, or "any store that has an enabled settings file", so a new store needs no change in Ringg. Each agent has its own secret key.

**How the voice agent speaks:**

- Short sentences, one idea at a time.
- Prices said as words, never web addresses or long product codes.
- At most three products, then it offers to continue.
- It does not volunteer the store's phone number or email, but it gives them when the caller asks, using the store's real details.
- No order lookups, and no coupons.

**Where we are today:**

- A first agent (named b3) was built by hand for South Jackson only. A test conversation behaved correctly: it refused order lookups, invented no coupons and sent the caller to the store.
- A second agent, **B3 DEV**, was created from a saved specification by a script, as the shared multi-store version. It has an English voice, the two store variables, the website domains and the webhook set.
- The assistant side was tested directly for B3 DEV and answered correctly.
- **B3 DEV's ten tools are not attached yet.** Ringg's regular API has no endpoint for tools, so they are entered in Ringg's dashboard or through Ringg's workspace MCP server. Our setup script prints the exact list of tools and settings to enter. A script that creates them automatically through the MCP server is not in the code yet.
- The client is expected to provide a paid Ringg key. We plan one key with separate development and production agents. Whether the client will issue one key or two is not confirmed.

**Not yet verified:** that the store ID can be passed as a variable header on every channel, that Ringg fills the store name into the greeting, and which website address forms the widget accepts. The Ringg voice widget is not yet placed on the store's website, so how it passes the store variables on a real store page is not tested.

**Not built yet:** a job that fetches Ringg's call history to fill in any call events our webhook missed, outbound follow-up calls, and handing a chat over to a call.

## 10. Measuring the journey

- **Recorded today:** one event per chat turn and per search, and call events from Ringg, saved to daily files. No message text, replies or personal identifiers are stored.
- **Not built:** a central place for the events, the dashboard views, and the filtering of bots and foreign traffic, so AI numbers are not inflated (roadmap Priority #1).
- **Still to design:** the IDs that link a call or chat to an inquiry, then a lead, then an order.

## 11. Status at a glance

| Area | Status |
| --- | --- |
| Shared assistant service, store settings, refusal of unconfigured stores | Done |
| Tools and API connection, policy and product knowledge, orders for logged-in customers | Done |
| Reply checks, service key, limits, funnel events | Done |
| Voice prompt for any kind of store, store-by-ID checks for voice | Done |
| Ringg agent B3 DEV (settings, webhook, domains) | Done |
| Ringg tools for B3 DEV | Not attached yet: tool list ready, to be entered in Ringg |
| Chat widget and the backend route that calls the assistant | Done (in the storefront app) |
| Settings files for the first stores, store knowledge loading, server setup guide | Done |
| Hosting on AWS (where it runs, who deploys) | Not decided |
| Ringg voice widget on the store's website | Not started |
| Ringg agent role (inbound, outbound or both) | Waiting for the client |
| Central event store and dashboard | Not started |

## 12. Decisions and questions still open

**For the client**

- What should the Ringg agent do: take inbound calls, make outbound follow-up calls, or hand off from the chat? On which phone number or widget?
- Which AI provider is approved, and what is the monthly budget?
- Do the stores allow guest checkout? Orders are for logged-in customers only.
- May any chat text ever be stored, or only events?
- Will development and production use one Ringg workspace and key, or two?

**For the team**

- Where should events live so the dashboard can read them, and should the dashboard sit in the API repo or a separate app?
- Where does the assistant run on AWS, and who deploys it?
- Who creates a new store's tenant entry and database?

**For the API team**

- Share one real logged-in customer's orders response, so the order fields (and item quantities) can be confirmed. Today they are tested only against a made-up example.
- Confirm the storefront address of a blog post. The assistant assumes /blogs/{slug}, so blogs stay switched off for now.
- Agree on how the assistant proves who it is to the API (the header name and key).
- Share the response fields once customer-group prices and monthly or weekly pricing are added.

## 13. Findings for the API team

These came from code reads, not from running the systems.

**Fixed by the API team**

- The order update route now checks that a customer is logged in and that the order is theirs.
- The public stores response no longer includes the website's bot-check secret or the other secret keys. If the old secret was live, it should still be rotated.
- Product responses no longer include the base price (which looked like wholesale cost). The assistant never repeated it.
- The cache now uses a per-store prefix, and the category product list's cache key was fixed.
- A public shipping options endpoint was added.

**Still to check**

- A code read of the API's example settings file reports what looks like a real cloud access key pair. Treat it as exposed and rotate it. The key is deliberately not repeated here.
- The API signs admin logins with a secret that has a built-in fallback value when the setting is missing. The API should refuse to start without it.
- Some seeded FAQ text mentions UPI and cash on delivery. Review it before using it for a US store.

## Glossary

- **API:** the store's back office. The assistant asks it for products, prices and store details.
- **Store-UUID:** the unique ID of a store.
- **Tenant:** a store's entry in the master list, which holds the login for that store's own database.
- **RAG database:** the search database holding copies of each store's pages, FAQs and blogs.
- **Tool:** one thing the AI model can ask the assistant to do, such as search products.
- **Webhook:** an address that receives a message when something happens, such as a call ending.
- **MCP server:** a standard way for software to act in another service, here Ringg's workspace.
- **Fail closed:** when something is missing or switched off, the answer is no, not a guess.
