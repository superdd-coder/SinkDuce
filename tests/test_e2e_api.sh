#!/bin/bash
# End-to-end API test for embedding/rerank provider flow.
# Run: bash tests/test_e2e_api.sh
# Requires: curl, jq, running app. Set SINKDUCE_API_PORT to override default 18900.

set -e
PORT="${SINKDUCE_API_PORT:-18900}"
BASE="http://localhost:${PORT}/api"
PASS=0
FAIL=0

check() {
  local desc="$1" expected="$2" actual="$3"
  if [ "$actual" = "$expected" ]; then
    echo "  ✓ $desc"
    PASS=$((PASS + 1))
  else
    echo "  ✗ $desc (expected: $expected, got: $actual)"
    FAIL=$((FAIL + 1))
  fi
}

check_contains() {
  local desc="$1" haystack="$2" needle="$3"
  if echo "$haystack" | grep -q "$needle"; then
    echo "  ✓ $desc"
    PASS=$((PASS + 1))
  else
    echo "  ✗ $desc (expected to contain: $needle)"
    FAIL=$((FAIL + 1))
  fi
}

echo "=== 1. Embedding Provider CRUD ==="

# Create
RES=$(curl -s -X POST "$BASE/embedding/providers" -H "Content-Type: application/json" -d '{
  "name": "Test Emb", "provider": "remote", "model": "text-embedding-3-small",
  "base_url": "https://api.openai.com/v1", "api_key": "sk-test", "batch_size": 10, "is_default": true
}')
EMB_ID=$(echo "$RES" | jq -r '.id')
check "Create embedding provider" "Test Emb" "$(echo "$RES" | jq -r '.name')"
check "is_default set" "true" "$(echo "$RES" | jq -r '.is_default')"

# List - verify created provider is there
RES=$(curl -s "$BASE/embedding/providers")
FOUND=$(echo "$RES" | jq -r '.[] | select(.name=="Test Emb") | .name')
check "Created provider in list" "Test Emb" "$FOUND"

# Update
RES=$(curl -s -X PUT "$BASE/embedding/providers/$EMB_ID" -H "Content-Type: application/json" -d '{
  "name": "Test Emb Updated", "provider": "remote", "model": "text-embedding-3-small",
  "batch_size": 20
}')
check "Update embedding name" "Test Emb Updated" "$(echo "$RES" | jq -r '.name')"

# Create second (not default)
RES2=$(curl -s -X POST "$BASE/embedding/providers" -H "Content-Type: application/json" -d '{
  "name": "Local Emb", "provider": "local", "model": "BAAI/bge-small-zh-v1.5", "batch_size": 10
}')
LOCAL_ID=$(echo "$RES2" | jq -r '.id')

# Set default to second
curl -s -X POST "$BASE/embedding/providers/$LOCAL_ID/set-default" > /dev/null
RES=$(curl -s "$BASE/embedding/providers")
DEFAULT_NAME=$(echo "$RES" | jq -r '.[] | select(.is_default==true) | .name')
check "Set default embedding" "Local Emb" "$DEFAULT_NAME"

# Delete first
RES=$(curl -s -X DELETE "$BASE/embedding/providers/$EMB_ID")
check_contains "Delete embedding provider" "$RES" "deleted"

echo ""
echo "=== 2. Rerank Provider CRUD ==="

# Clean up any existing Test Rerank providers first
# First, find a non-Test-Rerank provider to set as default if needed
OTHER_RERANK=$(curl -s "$BASE/rerank/providers" | jq -r '.[] | select(.name!="Test Rerank") | .id' | head -1)
EXISTING_IDS=$(curl -s "$BASE/rerank/providers" | jq -r '.[] | select(.name=="Test Rerank") | .id')
for id in $EXISTING_IDS; do
  # If this is the default, set another provider as default first
  IS_DEFAULT=$(curl -s "$BASE/rerank/providers" | jq -r ".[] | select(.id==\"$id\") | .is_default")
  if [ "$IS_DEFAULT" = "true" ] && [ -n "$OTHER_RERANK" ]; then
    curl -s -X POST "$BASE/rerank/providers/$OTHER_RERANK/set-default" > /dev/null 2>&1 || true
  fi
  curl -s -X DELETE "$BASE/rerank/providers/$id" > /dev/null 2>&1 || true
done

# Create
RES=$(curl -s -X POST "$BASE/rerank/providers" -H "Content-Type: application/json" -d '{
  "name": "Test Rerank", "provider": "cohere", "model": "rerank-multilingual-v3.0",
  "base_url": "https://api.cohere.com/v1", "api_key": "sk-test", "is_default": true
}')
RERANK_ID=$(echo "$RES" | jq -r '.id')
check "Create rerank provider" "Test Rerank" "$(echo "$RES" | jq -r '.name')"

# List - verify only one Test Rerank exists
RES=$(curl -s "$BASE/rerank/providers")
RERANK_COUNT=$(echo "$RES" | jq '[.[] | select(.name=="Test Rerank")] | length')
check "Only one Test Rerank provider" "1" "$RERANK_COUNT"

# Delete - need to set another provider as default first if this is the default
IS_DEFAULT=$(echo "$RES" | jq -r ".[] | select(.id==\"$RERANK_ID\") | .is_default")
if [ "$IS_DEFAULT" = "true" ]; then
  OTHER_RERANK=$(echo "$RES" | jq -r '.[] | select(.id!="'$RERANK_ID'") | .id' | head -1)
  if [ -n "$OTHER_RERANK" ]; then
    curl -s -X POST "$BASE/rerank/providers/$OTHER_RERANK/set-default" > /dev/null
  fi
fi
RES=$(curl -s -X DELETE "$BASE/rerank/providers/$RERANK_ID")
check_contains "Delete rerank provider" "$RES" "deleted"

echo ""
echo "=== 3. Collection Embedding Provider Selection ==="

# Set embedding_provider_id on collection
RES=$(curl -s -X PUT "$BASE/collections/default/config" -H "Content-Type: application/json" -d '{
  "embedding_provider_id": "'$LOCAL_ID'"
}')
check_contains "Set collection embedding_provider_id" "$RES" "config"

# Verify it's stored
RES=$(curl -s "$BASE/collections/default/config")
STORED_ID=$(echo "$RES" | jq -r '.embedding_provider_id // empty')
check "Collection config has embedding_provider_id" "$LOCAL_ID" "$STORED_ID"

# Clear it
curl -s -X PUT "$BASE/collections/default/config" -H "Content-Type: application/json" -d '{
  "embedding_provider_id": null
}' > /dev/null

echo ""
echo "=== 4. Recall Rerank Provider Override ==="

# Create a rerank provider for override test
RES=$(curl -s -X POST "$BASE/rerank/providers" -H "Content-Type: application/json" -d '{
  "name": "Override Rerank", "provider": "cohere", "model": "rerank-v3",
  "base_url": "https://api.cohere.com/v1", "api_key": "sk-test", "is_default": false
}')
OVERRIDE_ID=$(echo "$RES" | jq -r '.id')

# Search with rerank_provider_id
RES=$(curl -s -X POST "$BASE/recall/search" -H "Content-Type: application/json" -d '{
  "query": "test query", "collections": ["default"], "top_k": 5,
  "use_reranker": true, "rerank_provider_id": "'$OVERRIDE_ID'"
}')
# Should not error (reranker may fail connection but the parameter is accepted)
check_contains "Search with rerank_provider_id accepted" "$RES" "results"

# Search without rerank_provider_id
RES=$(curl -s -X POST "$BASE/recall/search" -H "Content-Type: application/json" -d '{
  "query": "test query", "collections": ["default"], "top_k": 5, "use_reranker": false
}')
check_contains "Search without rerank_provider_id accepted" "$RES" "results"

# Cleanup
curl -s -X DELETE "$BASE/rerank/providers/$OVERRIDE_ID" > /dev/null
curl -s -X DELETE "$BASE/embedding/providers/$LOCAL_ID" > /dev/null

echo ""
echo "=== Results ==="
echo "Passed: $PASS"
echo "Failed: $FAIL"
[ $FAIL -eq 0 ] && echo "All tests passed!" || echo "Some tests failed."
exit $FAIL
