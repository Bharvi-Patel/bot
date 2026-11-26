<?php
/*
Create (or remove) a TEST customer with three orders in a LOCAL copy of a store's database, so the bot's order lookup can be
tested without a real customer's account and without the storefront (whose captcha does not load on localhost).

    php dev_tools\seed_test_orders.php                        create bot-test@example.test with 3 orders (pending, shipped, delivered)
    php dev_tools\seed_test_orders.php --remove               delete that customer and every order it has
    php dev_tools\seed_test_orders.php --email x@example.test --store <uuid> --api <path to the api folder>

Then capture the real API response for it:   php dev_tools\capture_orders.php --email bot-test@example.test

Safety: refuses to run unless the API's environment is "local", and only accepts @example.* addresses (reserved test
domains), so it can never touch a real customer. The orders use real products of the store (name, SKU, current price) and
obviously fake shipping details. The customer's password is a random value nobody knows: log in with capture_orders.php,
which issues the token directly.
*/

$opts = getopt("", ["email:", "store:", "api:", "remove"]);
$email = strtolower($opts["email"] ?? "bot-test@example.test");
$store = $opts["store"] ?? "56695974416925887669406417382718";
$apiPath = $opts["api"] ?? dirname(__DIR__, 2) . DIRECTORY_SEPARATOR . "api";

if (!preg_match('/^[a-z0-9._+-]+@example\.(test|com|org|net)$/', $email)) {
    fwrite(STDERR, "Only test addresses @example.test/.com/.org/.net are allowed.\n");
    exit(2);
}
require $apiPath . "/vendor/autoload.php";
$app = require $apiPath . "/bootstrap/app.php";
$kernel = $app->make(Illuminate\Contracts\Http\Kernel::class);
$req = Illuminate\Http\Request::create("/stores", "GET", [], [], [], ["HTTP_STORE_UUID" => $store, "HTTP_ACCEPT" => "application/json"]);
$app->instance("request", $req);
$kernel->bootstrap();
if (!$app->environment("local")) {
    fwrite(STDERR, "Refusing: the API's environment is '" . $app->environment() . "', not 'local'.\n");
    exit(1);
}
if ($kernel->handle($req)->getStatusCode() !== 200) { fwrite(STDERR, "The API did not answer for store $store.\n"); exit(1); }

use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Hash;

$db = DB::connection("mysql");
$customer = $db->table("customers")->where("email", $email)->first();

if (isset($opts["remove"])) {
    if (!$customer) { echo "No customer $email: nothing to remove.\n"; exit(0); }
    $db->transaction(function () use ($db, $customer) {
        $orderIds = $db->table("orders")->where("customer_id", $customer->uuid)->pluck("uuid");
        $details = $db->table("order_details")->whereIn("order_id", $orderIds)->delete();
        $orders = $db->table("orders")->whereIn("uuid", $orderIds)->delete();
        $db->table("customers")->where("uuid", $customer->uuid)->delete();
        echo "Removed $email: $orders orders, $details order lines.\n";
    });
    exit(0);
}

if ($customer && $db->table("orders")->where("customer_id", $customer->uuid)->exists()) {
    echo "$email already has orders. Run with --remove first to start again.\n";
    exit(0);
}

$id32 = fn() => implode("", array_map(fn() => (string) random_int(0, 9), range(1, 32)));   // same shape as the store's own uuids
$status = fn(string $slug) => $db->table("order_statuses")->where("slug", $slug)->value("uuid");
$us = $db->table("countries")->where("iso_code", "US")->orWhere("iso_code", "USA")->value("uuid");

// Real, live products of this store with a price, and their names in the first language.
$products = $db->table("products as p")
    ->join("product_languages as pl", "pl.product_id", "=", "p.uuid")
    ->whereNull("p.deleted_at")->where("p.status", 1)->where("p.sale_price", ">", 50)->whereNotNull("p.sku")
    ->select("p.uuid", "p.sku", "p.sale_price", "pl.name")->orderBy("p.id")->limit(200)->get()
    ->unique("uuid")->values();
if ($products->count() < 4) { fwrite(STDERR, "Not enough live products to build orders.\n"); exit(1); }
$pick = fn(int $i) => $products[($i * 37) % $products->count()];

$db->transaction(function () use ($db, $customer, $email, $id32, $status, $us, $pick) {
    if (!$customer) {
        $uuid = $id32();
        $db->table("customers")->insert([
            "uuid" => $uuid, "first_name" => "Bot", "last_name" => "Test", "phone_number" => "000-000-0000", "email" => $email,
            "email_verified_at" => now(), "password" => Hash::make(bin2hex(random_bytes(24))),   // nobody knows it
            "created_at" => now(), "updated_at" => now(),
        ]);
        $customer = $db->table("customers")->where("uuid", $uuid)->first();
    }
    $next = (int) $db->table("orders")->max(DB::raw("CAST(order_number AS UNSIGNED)"));
    $plan = [["pending", 2, [[0, 1]]], ["shipped", 9, [[1, 1], [2, 2]]], ["delivered", 40, [[3, 1]]]];   // status, days ago, [product, qty]
    $fake = ["first_name" => "Bot", "last_name" => "Test", "email" => $email, "address_1" => "1 Test Street", "city" => "Jackson",
             "state" => "TN", "country_id" => $us, "zipcode" => "00000", "phone" => "000-000-0000"];
    foreach ($plan as $n => [$slug, $daysAgo, $lines]) {
        $orderUuid = $id32();
        $at = now()->subDays($daysAgo);
        $subTotal = 0.0;
        foreach ($lines as [$i, $qty]) {
            $p = $pick($n * 3 + $i);
            $subTotal += (float) $p->sale_price * $qty;
            $db->table("order_details")->insert([
                "order_id" => $orderUuid, "product_id" => $p->uuid, "product_name" => $p->name, "product_sku" => $p->sku,
                "product_qty" => $qty, "base_price" => $p->sale_price, "sale_price" => $p->sale_price,   // base_price set to the sale price: no real cost
                "created_at" => $at, "updated_at" => $at,
            ]);
        }
        $row = ["uuid" => $orderUuid, "customer_id" => $customer->uuid, "order_number" => (string) ($next + $n + 1),
                "sub_total" => $subTotal, "total_tax" => 0, "shipping_charge" => 0, "total_discount" => 0, "grand_total" => $subTotal,
                "order_status_id" => $status($slug), "payment_status_id" => $status($slug === "pending" ? "pending" : "paid"),
                "payment_status" => $slug === "pending" ? "pending" : "paid", "status" => 1, "created_at" => $at, "updated_at" => $at];
        foreach ($fake as $k => $v) { $row["shipping_$k"] = $v; $row["billing_$k"] = $v; }
        $db->table("orders")->insert($row);
        printf("  order %s  %-9s  %d line(s)  total %.2f\n", $row["order_number"], $slug, count($lines), $subTotal);
    }
});
echo "Created test customer $email with 3 orders.\n";
echo "Next:  php dev_tools\\capture_orders.php --email $email\n";
