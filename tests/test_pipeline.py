# -*- coding: utf-8 -*-
"""
    tests.pipeline
    --------------

    Test Emmett pipeline

    :copyright: (c) 2014-2019 by Giovanni Barillari
    :license: BSD, see LICENSE for more details.
"""

import asyncio
import pytest

from contextlib import contextmanager

from helpers import current_ctx as _current_ctx, ws_ctx as _ws_ctx
from emmett import App, request, websocket, abort
from emmett.ctx import current
from emmett.http import HTTP
from emmett.pipeline import Pipe


class PipeException(Exception):
    def __init__(self, pipe):
        self.pipe = pipe


class FlowStorePipe(Pipe):
    @property
    def linear_storage(self):
        return current._pipeline_linear_storage

    @property
    def parallel_storage(self):
        return current._pipeline_parallel_storage

    def store_linear(self, status):
        self.linear_storage.append(self.__class__.__name__ + "." + status)

    def store_parallel(self, status):
        self.parallel_storage.append(self.__class__.__name__ + "." + status)

    async def open(self):
        self.store_parallel('open')

    async def pipe(self, next_pipe, **kwargs):
        self.store_linear('pipe')
        return await next_pipe(**kwargs)

    async def pipe_ws(self, next_pipe, **kwargs):
        self.store_linear('pipe_ws')
        await next_pipe(**kwargs)

    async def on_pipe_success(self):
        self.store_linear('success')

    async def on_pipe_failure(self):
        self.store_linear('failure')

    def on_receive(self, data):
        self.store_linear('receive')
        return data

    def on_send(self, data):
        self.store_linear('send')
        return data

    async def close(self):
        self.store_parallel('close')


class Pipe1(FlowStorePipe):
    pass


class Pipe2(FlowStorePipe):
    async def pipe(self, next_pipe, **kwargs):
        self.store_linear('pipe')
        if request.query_params.skip:
            return "block"
        return await next_pipe(**kwargs)

    async def pipe_ws(self, next_pipe, **kwargs):
        self.store_linear('pipe_ws')
        if websocket.query_params.skip:
            return
        await next_pipe(**kwargs)


class Pipe3(FlowStorePipe):
    async def open(self):
        await asyncio.sleep(0.05)
        await super().open()


class Pipe4(FlowStorePipe):
    async def close(self):
        await asyncio.sleep(0.05)
        await super().close()


class Pipe5(FlowStorePipe):
    pass


class Pipe6(FlowStorePipe):
    pass


class ExcPipeOpen(FlowStorePipe):
    async def open(self):
        raise PipeException(self)


class ExcPipeClose(FlowStorePipe):
    async def close(self):
        raise PipeException(self)


@contextmanager
def request_ctx(path):
    with _current_ctx(path) as ctx:
        ctx._pipeline_linear_storage = []
        ctx._pipeline_parallel_storage = []
        yield ctx


@contextmanager
def ws_ctx(path):
    with _ws_ctx(path) as ctx:
        ctx._pipeline_linear_storage = []
        ctx._pipeline_parallel_storage = []
        yield ctx


def linear_flows_are_equal(flow, ctx):
    try:
        for index, value in enumerate(flow):
            if ctx._pipeline_linear_storage[index] != value:
                return False
    except Exception:
        return False
    return True


def parallel_flows_are_equal(flow, ctx):
    return set(flow) == set(ctx._pipeline_parallel_storage)


@pytest.fixture(scope='module')
def app():
    app = App(__name__)
    app.pipeline = [Pipe1(), Pipe2(), Pipe3()]

    @app.route()
    def ok():
        return "ok"

    @app.route()
    def http_error():
        abort(422)

    @app.route()
    def error():
        raise Exception

    @app.route(pipeline=[ExcPipeOpen(), Pipe4()])
    def open_error():
        return ''

    @app.route(pipeline=[ExcPipeClose(), Pipe4()])
    def close_error():
        return ''

    @app.route(pipeline=[Pipe4()])
    def pipe4():
        return "4"

    @app.websocket()
    async def ws_ok():
        await websocket.send('ok')

    @app.websocket()
    def ws_error():
        raise Exception

    @app.websocket(pipeline=[ExcPipeOpen(), Pipe4()])
    def ws_open_error():
        return

    @app.websocket(pipeline=[ExcPipeClose(), Pipe4()])
    def ws_close_error():
        return

    @app.websocket(pipeline=[Pipe4()])
    def ws_pipe4():
        return

    mod = app.module(__name__, 'mod', url_prefix='mod')
    mod.pipeline = [Pipe5()]

    @mod.route()
    def pipe5():
        return "5"

    @mod.route(pipeline=[Pipe6()])
    def pipe6():
        return "6"

    @mod.websocket()
    def ws_pipe5():
        return

    @mod.websocket(pipeline=[Pipe6()])
    def ws_pipe6():
        return

    return app


@pytest.mark.asyncio
async def test_ok_flow(app):
    with request_ctx('/ok') as ctx:
        parallel_flow = [
            'Pipe1.open', 'Pipe2.open', 'Pipe3.open',
            'Pipe3.close', 'Pipe2.close', 'Pipe1.close']
        linear_flow = [
            'Pipe1.pipe', 'Pipe2.pipe', 'Pipe3.pipe',
            'Pipe3.success', 'Pipe2.success', 'Pipe1.success']
        await app._router_http.dispatch()
        assert linear_flows_are_equal(linear_flow, ctx)
        assert parallel_flows_are_equal(parallel_flow, ctx)

    with ws_ctx('/ws_ok') as ctx:
        parallel_flow = [
            'Pipe1.open', 'Pipe2.open', 'Pipe3.open',
            'Pipe3.close', 'Pipe2.close', 'Pipe1.close']
        linear_flow = [
            'Pipe1.pipe_ws', 'Pipe2.pipe_ws', 'Pipe3.pipe_ws',
            'Pipe3.success', 'Pipe2.success', 'Pipe1.success']
        await app._router_ws.dispatch()
        assert linear_flows_are_equal(linear_flow, ctx)
        assert parallel_flows_are_equal(parallel_flow, ctx)


@pytest.mark.asyncio
async def test_httperror_flow(app):
    with request_ctx('/http_error') as ctx:
        parallel_flow = [
            'Pipe1.open', 'Pipe2.open', 'Pipe3.open',
            'Pipe3.close', 'Pipe2.close', 'Pipe1.close']
        linear_flow = [
            'Pipe1.pipe', 'Pipe2.pipe', 'Pipe3.pipe',
            'Pipe3.success', 'Pipe2.success', 'Pipe1.success']
        try:
            await app._router_http.dispatch()
        except HTTP:
            pass
        assert linear_flows_are_equal(linear_flow, ctx)
        assert parallel_flows_are_equal(parallel_flow, ctx)


@pytest.mark.asyncio
async def test_error_flow(app):
    with request_ctx('/error') as ctx:
        parallel_flow = [
            'Pipe1.open', 'Pipe2.open', 'Pipe3.open',
            'Pipe3.close', 'Pipe2.close', 'Pipe1.close']
        linear_flow = [
            'Pipe1.pipe', 'Pipe2.pipe', 'Pipe3.pipe',
            'Pipe3.failure', 'Pipe2.failure', 'Pipe1.failure']
        try:
            await app._router_http.dispatch()
        except Exception:
            pass
        assert linear_flows_are_equal(linear_flow, ctx)
        assert parallel_flows_are_equal(parallel_flow, ctx)

    with ws_ctx('/ws_error') as ctx:
        parallel_flow = [
            'Pipe1.open', 'Pipe2.open', 'Pipe3.open',
            'Pipe3.close', 'Pipe2.close', 'Pipe1.close']
        linear_flow = [
            'Pipe1.pipe_ws', 'Pipe2.pipe_ws', 'Pipe3.pipe_ws',
            'Pipe3.failure', 'Pipe2.failure', 'Pipe1.failure']
        try:
            await app._router_ws.dispatch()
        except Exception:
            pass
        assert linear_flows_are_equal(linear_flow, ctx)
        assert parallel_flows_are_equal(parallel_flow, ctx)


@pytest.mark.asyncio
async def test_open_error(app):
    with request_ctx('/open_error') as ctx:
        parallel_flow = [
            'Pipe1.open', 'Pipe2.open', 'Pipe3.open', 'Pipe4.open']
        linear_flow = []
        try:
            await app._router_http.dispatch()
        except PipeException as e:
            assert isinstance(e.pipe, ExcPipeOpen)
        assert linear_flows_are_equal(linear_flow, ctx)
        assert parallel_flows_are_equal(parallel_flow, ctx)

    with ws_ctx('/ws_open_error') as ctx:
        parallel_flow = [
            'Pipe1.open', 'Pipe2.open', 'Pipe3.open', 'Pipe4.open']
        linear_flow = []
        try:
            await app._router_ws.dispatch()
        except PipeException as e:
            assert isinstance(e.pipe, ExcPipeOpen)
        assert linear_flows_are_equal(linear_flow, ctx)
        assert parallel_flows_are_equal(parallel_flow, ctx)


@pytest.mark.asyncio
async def test_close_error(app):
    with request_ctx('/close_error') as ctx:
        parallel_flow = [
            'Pipe1.open', 'Pipe2.open', 'Pipe3.open', 'ExcPipeClose.open',
            'Pipe4.open',
            'Pipe4.close', 'Pipe3.close', 'Pipe2.close', 'Pipe1.close']
        linear_flow = [
            'Pipe1.pipe', 'Pipe2.pipe', 'Pipe3.pipe', 'ExcPipeClose.pipe',
            'Pipe4.pipe',
            'Pipe4.success', 'ExcPipeClose.success', 'Pipe3.success',
            'Pipe2.success', 'Pipe1.success']
        try:
            await app._router_http.dispatch()
        except PipeException as e:
            assert isinstance(e.pipe, ExcPipeClose)
        assert linear_flows_are_equal(linear_flow, ctx)
        assert parallel_flows_are_equal(parallel_flow, ctx)

    with ws_ctx('/ws_close_error') as ctx:
        parallel_flow = [
            'Pipe1.open', 'Pipe2.open', 'Pipe3.open', 'ExcPipeClose.open',
            'Pipe4.open',
            'Pipe4.close', 'Pipe3.close', 'Pipe2.close', 'Pipe1.close']
        linear_flow = [
            'Pipe1.pipe_ws', 'Pipe2.pipe_ws', 'Pipe3.pipe_ws',
            'ExcPipeClose.pipe_ws', 'Pipe4.pipe_ws',
            'Pipe4.success', 'ExcPipeClose.success', 'Pipe3.success',
            'Pipe2.success', 'Pipe1.success']
        try:
            await app._router_ws.dispatch()
        except PipeException as e:
            assert isinstance(e.pipe, ExcPipeClose)
        assert linear_flows_are_equal(linear_flow, ctx)
        assert parallel_flows_are_equal(parallel_flow, ctx)


@pytest.mark.asyncio
async def test_flow_interrupt(app):
    with request_ctx('/ok?skip=yes') as ctx:
        parallel_flow = [
            'Pipe1.open', 'Pipe2.open', 'Pipe3.open',
            'Pipe3.close', 'Pipe2.close', 'Pipe1.close']
        linear_flow = [
            'Pipe1.pipe', 'Pipe2.pipe',
            'Pipe2.success', 'Pipe1.success']
        await app._router_http.dispatch()
        assert linear_flows_are_equal(linear_flow, ctx)
        assert parallel_flows_are_equal(parallel_flow, ctx)

    with ws_ctx('/ws_ok?skip=yes') as ctx:
        parallel_flow = [
            'Pipe1.open', 'Pipe2.open', 'Pipe3.open',
            'Pipe3.close', 'Pipe2.close', 'Pipe1.close']
        linear_flow = [
            'Pipe1.pipe_ws', 'Pipe2.pipe_ws',
            'Pipe2.success', 'Pipe1.success']
        await app._router_ws.dispatch()
        assert linear_flows_are_equal(linear_flow, ctx)
        assert parallel_flows_are_equal(parallel_flow, ctx)


@pytest.mark.asyncio
async def test_pipeline_composition(app):
    with request_ctx('/pipe4') as ctx:
        parallel_flow = [
            'Pipe1.open', 'Pipe2.open', 'Pipe3.open', 'Pipe4.open',
            'Pipe4.close', 'Pipe3.close', 'Pipe2.close', 'Pipe1.close']
        linear_flow = [
            'Pipe1.pipe', 'Pipe2.pipe', 'Pipe3.pipe', 'Pipe4.pipe',
            'Pipe4.success', 'Pipe3.success', 'Pipe2.success', 'Pipe1.success']
        await app._router_http.dispatch()
        assert linear_flows_are_equal(linear_flow, ctx)
        assert parallel_flows_are_equal(parallel_flow, ctx)

    with ws_ctx('/ws_pipe4') as ctx:
        parallel_flow = [
            'Pipe1.open', 'Pipe2.open', 'Pipe3.open', 'Pipe4.open',
            'Pipe4.close', 'Pipe3.close', 'Pipe2.close', 'Pipe1.close']
        linear_flow = [
            'Pipe1.pipe_ws', 'Pipe2.pipe_ws', 'Pipe3.pipe_ws', 'Pipe4.pipe_ws',
            'Pipe4.success', 'Pipe3.success', 'Pipe2.success', 'Pipe1.success']
        await app._router_ws.dispatch()
        assert linear_flows_are_equal(linear_flow, ctx)
        assert parallel_flows_are_equal(parallel_flow, ctx)


@pytest.mark.asyncio
async def test_module_pipeline(app):
    with request_ctx('/mod/pipe5') as ctx:
        parallel_flow = [
            'Pipe1.open', 'Pipe2.open', 'Pipe3.open', 'Pipe5.open',
            'Pipe5.close', 'Pipe3.close', 'Pipe2.close', 'Pipe1.close']
        linear_flow = [
            'Pipe1.pipe', 'Pipe2.pipe', 'Pipe3.pipe', 'Pipe5.pipe',
            'Pipe5.success', 'Pipe3.success', 'Pipe2.success', 'Pipe1.success']
        await app._router_http.dispatch()
        assert linear_flows_are_equal(linear_flow, ctx)
        assert parallel_flows_are_equal(parallel_flow, ctx)

    with ws_ctx('/mod/ws_pipe5') as ctx:
        parallel_flow = [
            'Pipe1.open', 'Pipe2.open', 'Pipe3.open', 'Pipe5.open',
            'Pipe5.close', 'Pipe3.close', 'Pipe2.close', 'Pipe1.close']
        linear_flow = [
            'Pipe1.pipe_ws', 'Pipe2.pipe_ws', 'Pipe3.pipe_ws', 'Pipe5.pipe_ws',
            'Pipe5.success', 'Pipe3.success', 'Pipe2.success', 'Pipe1.success']
        await app._router_ws.dispatch()
        assert linear_flows_are_equal(linear_flow, ctx)
        assert parallel_flows_are_equal(parallel_flow, ctx)


@pytest.mark.asyncio
async def test_module_pipeline_composition(app):
    with request_ctx('/mod/pipe6') as ctx:
        parallel_flow = [
            'Pipe1.open', 'Pipe2.open', 'Pipe3.open', 'Pipe5.open',
            'Pipe6.open',
            'Pipe6.close', 'Pipe5.close', 'Pipe3.close', 'Pipe2.close',
            'Pipe1.close']
        linear_flow = [
            'Pipe1.pipe', 'Pipe2.pipe', 'Pipe3.pipe', 'Pipe5.pipe',
            'Pipe6.pipe',
            'Pipe6.success', 'Pipe5.success', 'Pipe3.success', 'Pipe2.success',
            'Pipe1.success']
        await app._router_http.dispatch()
        assert linear_flows_are_equal(linear_flow, ctx)
        assert parallel_flows_are_equal(parallel_flow, ctx)

    with ws_ctx('/mod/ws_pipe6') as ctx:
        parallel_flow = [
            'Pipe1.open', 'Pipe2.open', 'Pipe3.open', 'Pipe5.open',
            'Pipe6.open',
            'Pipe6.close', 'Pipe5.close', 'Pipe3.close', 'Pipe2.close',
            'Pipe1.close']
        linear_flow = [
            'Pipe1.pipe_ws', 'Pipe2.pipe_ws', 'Pipe3.pipe_ws', 'Pipe5.pipe_ws',
            'Pipe6.pipe_ws',
            'Pipe6.success', 'Pipe5.success', 'Pipe3.success', 'Pipe2.success',
            'Pipe1.success']
        await app._router_ws.dispatch()
        assert linear_flows_are_equal(linear_flow, ctx)
        assert parallel_flows_are_equal(parallel_flow, ctx)
