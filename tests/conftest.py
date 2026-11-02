"""Minimal asyncio support for pytest (no pytest-asyncio dependency).

Runs each `async def` test, together with its async-generator fixtures, in a
fresh event loop.
"""
import asyncio
import inspect

import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "asyncio: run test in an event loop")


@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem):
    if not inspect.iscoroutinefunction(pyfuncitem.obj):
        return None
    loop = pyfuncitem._rako_loop
    kwargs = {name: pyfuncitem.funcargs[name] for name in pyfuncitem._fixtureinfo.argnames}
    loop.run_until_complete(pyfuncitem.obj(**kwargs))
    return True


@pytest.fixture(autouse=True)
def _rako_loop(request):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    request.node._rako_loop = loop
    yield loop
    loop.run_until_complete(loop.shutdown_asyncgens())
    loop.close()


@pytest.fixture
def hub(_rako_loop):
    from test_client import _hub_impl  # noqa: PLC0415
    gen = _hub_impl()
    value = _rako_loop.run_until_complete(gen.__anext__())
    yield value
    try:
        _rako_loop.run_until_complete(gen.__anext__())
    except StopAsyncIteration:
        pass
