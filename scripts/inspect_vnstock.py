"""Inspect installed SDK without making a market-data request."""
import inspect
import json


if __name__ == "__main__":
    import vnstock
    from vnstock import Market
    print(json.dumps({"version":vnstock.__version__,"quote_signature":str(inspect.signature(Market.quote)),
                      "login_signature":str(inspect.signature(vnstock.login)),"market_request_sent":False},indent=2))
