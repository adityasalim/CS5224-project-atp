from fakes import load

module = load("tp-verify-itinerary")
for currency in ("SGD","USD","SAR","EUR","GBP","CNY","JPY"):
    spec={"startDate":"2030-01-01","endDate":"2030-01-02","pax":1,
          "budget":{"amount":"100","currency":currency},
          "dailySpend":{"amount":"10","currency":currency}}
    result=module.verify(spec,{"days":[]},{},100)
    assert result["totals"]["currency"]==currency, result
    assert result["totals"]["total"]=="20", result
    assert "totalSGD" not in result["totals"], result
    bad={**spec,"dailySpend":{"amount":"10","currency":"XYZ"}}
    try:
        module.verify(bad,{"days":[]},{},100)
    except ValueError:
        pass
    else:
        raise AssertionError("Mixed currencies were accepted")
print("SELECTED CURRENCY VERIFICATION OK")
