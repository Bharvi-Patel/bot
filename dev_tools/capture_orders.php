<?php
/*
Capture a REAL customer orders response from the LOCAL store API, with personal data removed, so the bot's order code
(bot/api_client.py customer_orders) can be checked and tested against real field names instead of a made-up sample.

No password is needed: on a local copy of a store's database this boots the API in-process, finds a TEST customer by
email and issues that customer's login token the same way POST /login does after the password check
(Auth::guard('customer-api')->login). It then calls GET /orders and GET /orders/{uuid} with that token, exactly as the
bot would. The token never leaves this process. It refuses to run unless the API's environment is "local".

Run from the chatbot folder with Laragon's PHP 8.3 (the API sits next to it as ..\api):

    php dev_tools\capture_orders.php --list                       test customers (emails like %@example.%) and their order counts
    php dev_tools\capture_orders.php --email test@example.com     capture that customer's orders

Options:  --store <uuid>  (default South Jackson)   --api <path to the api folder>   --from-file <json>  (redact a file only)
          --like <SQL LIKE pattern>  which emails --list may show (default %@example.%)

Writes:  tests/fixtures/api/orders_index_real.json   (at most 3 orders)
         tests/fixtures/api/orders_show_real.json
Personal values become "[redacted]"; keys are kept, because the keys are what the bot depends on. Check both files before
committing them. Do not paste --list output anywhere: it shows customer emails.
*/

$opts = getopt("", ["email:", "store:", "api:", "list", "from-file:", "like:"]);
$like = $opts["like"] ?? "%@example.%";          // --list shows only test-looking addresses, never real customers
$store = $opts["store"] ?? "56695974416925887669406417382718";
$apiPath = $opts["api"] ?? dirname(__DIR__, 2) . DIRECTORY_SEPARATOR . "api";
$outDir = dirname(__DIR__) . DIRECTORY_SEPARATOR . "tests" . DIRECTORY_SEPARATOR . "fixtures" . DIRECTORY_SEPARATOR . "api";

// ---------- redaction ----------
const PII_KEY = '/^(.*_)?(email|phone|mobile|telephone|first_?name|last_?name|full_?name|customer_?name|user_?name|company|address(_?\d)?|street|city|zip|zip_?code|postal_?code|postcode|state_?name|country_?name|ip|ip_?address|card.*|cvv|cvc|iban|account_?number|transaction_?id|payment_?(id|intent|token|details|response|data)|token|access_?token|password|signature|note|notes|comment|comments|instructions?)$/i';
const PII_OBJECT = '/^(customer|user|billing|shipping_address|billing_address|address|addresses|payment|payments|card)$/i';
const KEEP_UNDER_BILLING_SHIPPING = '/(amount|charge|charges|cost|total|price|tax|method|method_id|type|status|id)$/i';

function is_pii_key(string $key): bool {
    if (preg_match(PII_KEY, $key)) return true;
    return (bool) (preg_match('/^(billing|shipping)_/i', $key) && !preg_match(KEEP_UNDER_BILLING_SHIPPING, $key));
}

function redact($node, bool $inside = false) {
    if (is_array($node)) {
        $out = [];
        foreach ($node as $k => $v) {
            $personal = $inside || (is_string($k) && preg_match(PII_OBJECT, $k));
            if (is_array($v)) {
                $out[$k] = redact($v, (bool) $personal);
            } elseif ($v !== null && ($personal || (is_string($k) && is_pii_key($k)))) {
                $out[$k] = "[redacted]";
            } else {
                $out[$k] = $v;
            }
        }
        return $out;
    }
    return ($inside && $node !== null) ? "[redacted]" : $node;
}

function summary(array $index): void {
    echo "\nWhat the bot reads from GET /orders (bot/api_client.py customer_orders):\n";
    if (!isset($index["orders"]) || !is_array($index["orders"])) {
        echo "  !! no top-level 'orders' list. Top-level keys are: " . implode(", ", array_keys($index)) . "\n";
        echo "     The bot expects {\"orders\": [...]}, so customer_orders needs changing to match.\n";
        return;
    }
    echo "  orders in the response: " . count($index["orders"]) . "\n";
    $o = $index["orders"][0] ?? null;
    if (!is_array($o)) { echo "  (no orders for this customer: pick another with --list)\n"; return; }
    $status = $o["order_status"] ?? ($o["orderStatus"] ?? null);
    $details = $o["order_details"] ?? ($o["orderDetails"] ?? []);
    $d0 = is_array($details) ? ($details[0] ?? null) : null;
    printf("  %-28s %s\n", "order keys:", implode(", ", array_keys($o)));
    printf("  %-28s %s\n", "order_status keys:", is_array($status) ? implode(", ", array_keys($status)) : "(missing)");
    printf("  %-28s %s\n\n", "order_details[0] keys:", is_array($d0) ? implode(", ", array_keys($d0)) : "(missing)");
    $checks = [
        "order_number" => isset($o["order_number"]),
        "order_status.name" => is_array($status) && isset($status["name"]),
        "created_at" => isset($o["created_at"]),
        "grand_total" => isset($o["grand_total"]),
        "order_details[].product_name" => is_array($d0) && isset($d0["product_name"]),
        "order_details[].product_sku" => is_array($d0) && array_key_exists("product_sku", $d0),
        "order_details[].quantity (not read yet)" => is_array($d0) && (isset($d0["quantity"]) || isset($d0["qty"])),
    ];
    foreach ($checks as $k => $ok) printf("  %s  %s\n", $ok ? "found  " : "MISSING", $k);
}

function save(string $path, $data): void {
    file_put_contents($path, json_encode($data, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE) . "\n");
    echo "Saved $path\n";
}

if (isset($opts["from-file"])) {
    $data = redact(json_decode(file_get_contents($opts["from-file"]), true));
    echo json_encode($data, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE), "\n";
    summary($data);
    exit(0);
}
if (!isset($opts["email"]) && !isset($opts["list"])) {
    fwrite(STDERR, "Give --email <test customer email>, or --list to see test customers that have orders.\n");
    exit(2);
}

// ---------- boot the local API for this store ----------
if (!is_file($apiPath . "/vendor/autoload.php")) { fwrite(STDERR, "No API found at $apiPath (use --api).\n"); exit(2); }
require $apiPath . "/vendor/autoload.php";
$app = require $apiPath . "/bootstrap/app.php";
$kernel = $app->make(Illuminate\Contracts\Http\Kernel::class);

function api_get($kernel, $app, string $uri, array $server) {
    $req = Illuminate\Http\Request::create($uri, "GET", [], [], [], $server + ["HTTP_ACCEPT" => "application/json"]);
    $app->instance("request", $req);
    $res = $kernel->handle($req);
    return [$res->getStatusCode(), json_decode($res->getContent(), true)];
}

$storeHeader = ["HTTP_STORE_UUID" => $store];
$first = Illuminate\Http\Request::create("/stores", "GET", [], [], [], $storeHeader + ["HTTP_ACCEPT" => "application/json"]);
$app->instance("request", $first);
$kernel->bootstrap();
if (!$app->environment("local")) {
    fwrite(STDERR, "Refusing: the API's environment is '" . $app->environment() . "', not 'local'. This only runs on a local copy.\n");
    exit(1);
}
[$code] = api_get($kernel, $app, "/stores", $storeHeader);          // runs the tenant middleware: this store's database
if ($code !== 200) { fwrite(STDERR, "The API did not answer for store $store (HTTP $code).\n"); exit(1); }

$customers = App\Models\Customer::query();

if (isset($opts["list"])) {
    // Only addresses matching --like are listed, so real customers' emails are never printed.
    $rows = Illuminate\Support\Facades\DB::connection("mysql")->table("customers as c")
        ->leftJoin("orders as o", "o.customer_id", "=", "c.uuid")
        ->where("c.email", "like", $like)->groupBy("c.email")
        ->select("c.email", Illuminate\Support\Facades\DB::raw("COUNT(o.id) AS n"))->orderByDesc("n")->limit(15)->get();
    foreach ($rows as $r) printf("  %3d orders  %s\n", $r->n, $r->email);
    if ($rows->isEmpty()) echo "  No customer with an email like $like in this store's database.\n";
    elseif ($rows->first()->n == 0) echo "  None of them has an order yet: place a test order on the storefront first.\n";
    exit(0);
}

$customer = App\Models\Customer::where("email", $opts["email"])->first();
if (!$customer) { fwrite(STDERR, "No customer with that email in store $store.\n"); exit(1); }

// The same token POST /login issues after the password check. It stays in this process.
$token = Illuminate\Support\Facades\Auth::guard("customer-api")->login($customer);
Illuminate\Support\Facades\Auth::forgetGuards();                     // so GET /orders authenticates from the token alone
$auth = $storeHeader + ["HTTP_AUTHORIZATION" => "Bearer " . $token];
echo "Token issued for that customer (not shown).\n";

[$code, $index] = api_get($kernel, $app, "/orders?page=1&limit=25", $auth);
if ($code !== 200 || !is_array($index)) { fwrite(STDERR, "GET /orders failed (HTTP $code).\n"); exit(1); }

$show = null;
$firstOrder = $index["orders"][0] ?? null;
if (is_array($firstOrder) && !empty($firstOrder["uuid"])) {
    Illuminate\Support\Facades\Auth::forgetGuards();
    [$code2, $show] = api_get($kernel, $app, "/orders/" . $firstOrder["uuid"], $auth);
    if ($code2 !== 200) { echo "GET /orders/{uuid} answered HTTP $code2; saving the list only.\n"; $show = null; }
}
$token = null; $auth = null;

if (isset($index["orders"]) && is_array($index["orders"])) $index["orders"] = array_slice($index["orders"], 0, 3);
$index = redact($index);
if (!is_dir($outDir)) mkdir($outDir, 0777, true);
save($outDir . DIRECTORY_SEPARATOR . "orders_index_real.json", $index);
if (is_array($show)) save($outDir . DIRECTORY_SEPARATOR . "orders_show_real.json", redact($show));
summary($index);
echo "\nOpen the saved files and check no personal data is left before committing them.\n";
