"""Dealer configuration.

Add or remove dealers here. Each entry is a dict; the `platform` field selects
which fetcher in `fetchers.py` runs.
"""

DEALERS = [
    {
        "key": "bellroad",
        "name": "Bell Road Toyota",
        "platform": "dealeron",
        "host": "www.bellroadtoyota.com",
        "client_id": 25003,
        "page_id": 2483377,
        "srp_path": "/certified-pre-owned.html",
    },
    {
        "key": "earnhardt",
        "name": "Earnhardt Toyota",
        "platform": "dealeron",
        "host": "www.earnhardttoyota.com",
        "client_id": 22083,
        "page_id": 1884736,
        "srp_path": "/certified-pre-owned.html",
    },
    {
        "key": "bigtwo",
        "name": "Big Two Toyota",
        "platform": "dealercom",
        "host": "www.bigtwotoyota.com",
        "site_id": "bigtwotoyota",
        "page_id": "bigtwotoyota_SITEBUILDER_INVENTORY_SEARCH_RESULTS_AUTO_CERTIFIED_USED_V1_1",
        "srp_path": "/certified-inventory/index.htm",
    },
    {
        "key": "avondale",
        "name": "Avondale Toyota",
        "platform": "dealercom",
        "host": "www.avondaletoyota.com",
        "site_id": "avondaletoyotascion",
        "page_id": "v9_INVENTORY_SEARCH_RESULTS_AUTO_CERTIFIED_USED_V1_1",
        "srp_path": "/certified-inventory/index.htm",
    },
    {
        "key": "camelback",
        "name": "Camelback Toyota",
        "platform": "dealercom",
        "host": "www.camelbacktoyota.com",
        "site_id": "camelbacktoyotavtg",
        "page_id": "camelbacktoyotavtg_SITEBUILDER_INVENTORY_SEARCH_RESULTS_AUTO_CERTIFIED_USED_V1_2",
        "srp_path": "/certified-inventory/index.htm",
    },
]
