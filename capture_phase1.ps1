# Captures the real API responses still missing for Phase 1 item 4. Run with the API server running on port 8001:
#   powershell -ExecutionPolicy Bypass -File capture_phase1.ps1
#
# It only READS from the API. It asks for a TEST customer's email and password (typed here, never saved or printed),
# logs in, and saves: the customer's orders and one order's detail (personal fields blanked out automatically),
# a search with an attribute filter, a category listing with an attribute filter, and the blog endpoints.
# Output goes to the folder phase1_captures. The login token is never written to a file.
# CHECK the two orders files before sending them: they should contain no real names, phones, emails or addresses.

$u = "56695974416925887669406417382718"
$b = "http://127.0.0.1:8001"
$out = Join-Path (Get-Location) "phase1_captures"
New-Item -ItemType Directory -Force -Path $out | Out-Null

function Call($method, $path, $body, $token) {
  $h = @{ "Store-UUID" = $u; "Accept" = "application/json" }
  if ($token) { $h["Authorization"] = "Bearer " + $token }
  try {
    if ($body) {
      $r = Invoke-WebRequest -UseBasicParsing -Method $method -Uri ($b + $path) -Headers $h -ContentType "application/json" -Body $body
    } else {
      $r = Invoke-WebRequest -UseBasicParsing -Method $method -Uri ($b + $path) -Headers $h
    }
    return $r.Content
  } catch {
    $msg = $_.Exception.Message
    if ($_.ErrorDetails -and $_.ErrorDetails.Message) { $msg = $_.ErrorDetails.Message }
    return ("ERROR: " + $msg)
  }
}

function Save($name, $text) { [System.IO.File]::WriteAllText((Join-Path $out $name), [string]$text) }

# --- blank out personal data (used only for the orders files) ---
$PersonalKey = '(?i)^(first_?name|last_?name|full_?name|customer_?name|username|email|phone|mobile|contact\w*|address\w*|street\w*|city|state|zip\w*|postal\w*|country|card\w*|payment\w*|stripe\w*|transaction\w*|token\w*|password\w*|ip|ip_address|billing\w*|shipping_address\w*|customer|user|notes?)$'
function Redact($node) {
  if ($null -eq $node) { return $null }
  if ($node -is [System.Array]) {
    $items = @()
    foreach ($i in $node) { $items += , (Redact $i) }
    return , $items
  }
  if ($node -is [System.Management.Automation.PSCustomObject]) {
    $copy = [ordered]@{}
    foreach ($p in $node.PSObject.Properties) {
      if ($p.Name -match $PersonalKey) { $copy[$p.Name] = "REDACTED" } else { $copy[$p.Name] = Redact $p.Value }
    }
    return [PSCustomObject]$copy
  }
  if (($node -is [string]) -and ($node -match '^[^@\s]+@[^@\s]+\.[^@\s]+$')) { return "REDACTED" }
  return $node
}
function SaveRedacted($name, $raw) {
  try {
    $j = ConvertFrom-Json $raw
    Save $name ((Redact $j) | ConvertTo-Json -Depth 30)
  } catch {
    Save $name ("COULD NOT PARSE; not saved for safety. Raw length: " + $raw.Length)
  }
}

function FindOrderUuid($node) {
  if ($null -eq $node) { return $null }
  if ($node -is [System.Array]) {
    foreach ($i in $node) { $r = FindOrderUuid $i; if ($r) { return $r } }
    return $null
  }
  if ($node -is [System.Management.Automation.PSCustomObject]) {
    $names = @($node.PSObject.Properties.Name)
    $hasNumber = ($names -contains "order_number") -or ($names -contains "order_no") -or ($names -contains "order_id") -or ($names -contains "order_code")
    if (($names -contains "uuid") -and $hasNumber) { return $node.uuid }
    foreach ($p in $node.PSObject.Properties) { $r = FindOrderUuid $p.Value; if ($r) { return $r } }
  }
  return $null
}

function FindSlug($node) {
  if ($null -eq $node) { return $null }
  if ($node -is [System.Array]) {
    foreach ($i in $node) { $r = FindSlug $i; if ($r) { return $r } }
    return $null
  }
  if ($node -is [System.Management.Automation.PSCustomObject]) {
    if (@($node.PSObject.Properties.Name) -contains "slug") { return $node.slug }
    foreach ($p in $node.PSObject.Properties) { $r = FindSlug $p.Value; if ($r) { return $r } }
  }
  return $null
}

# 1) Orders (needs a test customer login)
Write-Host "1/3 orders: log in as a TEST customer (the password is not saved or shown)"
$email = Read-Host "Test customer email"
$sec = Read-Host "Password" -AsSecureString
$pw = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto([System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
$loginBody = (@{ email = $email; password = $pw } | ConvertTo-Json)
$loginRaw = Call "POST" "/login" $loginBody $null
$pw = $null; $loginBody = $null
$token = $null
try {
  $lj = ConvertFrom-Json $loginRaw
  $token = $lj.access_token
  if (-not $token) { $token = $lj.data.access_token }
  if (-not $token) { $token = $lj.token }
  if (-not $token) { Write-Host ("Login did not return a token. Server said: " + $lj.message) }
} catch {
  Write-Host ("Login failed: " + ($loginRaw -replace '(?i)"(password|email)"\s*:\s*"[^"]*"', '"$1":"..."'))
}
if ($token) {
  $ordersRaw = Call "GET" "/orders?page=1&limit=5" $null $token
  SaveRedacted "20_orders_index_redacted.json" $ordersRaw
  $ouuid = $null
  try { $ouuid = FindOrderUuid (ConvertFrom-Json $ordersRaw) } catch { }
  if ($ouuid) {
    SaveRedacted "21_order_detail_redacted.json" (Call "GET" ("/orders/" + $ouuid) $null $token)
  } else {
    Write-Host "No order was found in the orders response (does this customer have orders?). The orders file is still saved."
  }
  $token = $null
}

# 2) Attribute filters: read the filter options from a search, then filter by the first attribute value
Write-Host "2/3 attribute filter"
$facetRaw = Call "POST" "/products/search" '{"q":"bed"}' $null
Save "30_search_bed_unfiltered.json" $facetRaw
$attrId = $null
$attrName = $null
try {
  $fj = ConvertFrom-Json $facetRaw
  foreach ($g in @($fj.data.filter.attributes)) {
    foreach ($a in @($g.attributes)) {
      if (-not $attrId) {
        if ($a.uuid) { $attrId = $a.uuid } elseif ($a.id) { $attrId = $a.id }
        $attrName = ([string]$g.group_name) + ": " + ([string]$a.name)
      }
    }
  }
} catch { }
if ($attrId) {
  Write-Host ("  using attribute " + $attrName)
  Save "31_search_bed_attribute.json" (Call "POST" "/products/search" ('{"q":"bed","attribute_ids":["' + $attrId + '"]}') $null)
  Save "32_category_beds_attribute.json" (Call "POST" "/categories/bedroom-furniture-beds/products" ('{"attribute_ids":["' + $attrId + '"],"price_range":{"from":0}}') $null)
} else {
  Write-Host "  no attribute options were found in the search response; the unfiltered search is saved"
}

# 3) Blogs
Write-Host "3/3 blogs"
Save "40_blogs.json" (Call "GET" "/blogs" $null $null)
Save "41_blogs_active.json" (Call "GET" "/blogs/active" $null $null)
$recentRaw = Call "GET" "/blogs/recent" $null $null
Save "42_blogs_recent.json" $recentRaw
$slug = $null
try { $slug = FindSlug (ConvertFrom-Json $recentRaw) } catch { }
if (-not $slug) { try { $slug = FindSlug (ConvertFrom-Json (Get-Content (Join-Path $out "41_blogs_active.json") -Raw)) } catch { } }
if ($slug) { Save "43_blog_detail.json" (Call "GET" ("/blogs/" + $slug) $null $null) } else { Write-Host "  no blog was found to fetch in detail" }

Write-Host ""
Write-Host ("Done. Files are in: " + $out)
Write-Host "Before uploading, open 20_orders_index_redacted.json and 21_order_detail_redacted.json and check no real personal data is left."
