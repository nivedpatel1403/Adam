from adam.pipeline.wiring import _network_endpoint


def test_explicit_observed_http_endpoint_wins_over_socket_address():
    assert _network_endpoint([
        {'DestinationIp':'192.168.19.2','DestinationPort':'18080'},
        {'network_endpoint':'http://192.168.19.2:18080/marker/c2'},
    ]) == 'http://192.168.19.2:18080/marker/c2'


def test_full_url_in_observed_process_command_line_is_preserved():
    assert _network_endpoint([{'CommandLine':
        "powershell -Command Invoke-WebRequest -Uri 'http://192.168.19.2:18080/marker/c2' -TimeoutSec 10"}]) == 'http://192.168.19.2:18080/marker/c2'


def test_bare_socket_does_not_invent_http_scheme():
    assert _network_endpoint([{'DestinationIp':'192.168.19.2','DestinationPort':'18080'}]) == '192.168.19.2:18080'
