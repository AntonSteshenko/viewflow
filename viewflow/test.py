"""
Flow scenario based testing

with FlowTest(RestrictedUserFlow) as flow_test:

    flow_test.Task(Flow.start).User('xxx').Execute({xxx=1, yyy=2}) \
        .Assert(lambda t: t.owner='xxx') \
        .Assert(p: p.owner='yyy')

    with patch('web.service'):
        flow_test.Task(Flow.job),Execute()

    flow_test.User('aaa').Task(Flow.confirm).Execute({'confirm': 1})

"""
import inspect
from singledispatch import singledispatch
from django_webtest import WebTestMixin

from viewflow import flow
from viewflow.models import Task
from viewflow.signals import task_finished
from viewflow.urls import node_url_reverse


@singledispatch
def flow_do(flow_node, test_task, **post_kwargs):
    """
    Executes flow task
    """
    raise NotImplementedError


@flow_do.register(flow.Start)  # NOQA
def _(flow_node, test_task, **post_kwargs):
    url_args = test_task.url_args.copy()
    url_args.setdefault('task', None)
    task_url = node_url_reverse(flow_node, **url_args)

    form = test_task.app.get(task_url, user=test_task.user).form
    form.submit('start', **post_kwargs).follow()


@flow_do.register(flow.View)  # NOQA
def _(flow_node, test_task, **post_kwargs):
    task = test_task.flow_cls.task_cls._default_manager.get(
        flow_task=test_task.flow_task,
        status=Task.STATUS.NEW)

    url_args = test_task.url_args.copy()
    url_args.setdefault('task', task)
    task_url = node_url_reverse(flow_node, **url_args)

    form = test_task.app.get(task_url, user=test_task.user).form
    for key, value in post_kwargs.items():
        form[key] = value

    form.submit().follow()


@flow_do.register(flow.Job)  # NOQA
def _(flow_node, test_task, **post_kwargs):
    pass


@singledispatch
def flow_patch_manager(flow_node):
    """
    context manager for flow mock setup
    """
    return None


@flow_patch_manager.register(flow.Job)  # NOQA
def _(flow_node):
    pass


class FlowTaskTest(object):
    def __init__(self, app, flow_cls, flow_task):
        self.app = app
        self.flow_cls, self.flow_task = flow_cls, flow_task
        self.process, self.task = None, None

        self.user = None
        self.url_args = {}

    def task_finished(self, sender, **kwargs):
        if self.task is None:
            """
            First finished task is ours
            """
            assert self.flow_task == kwargs['task'].flow_task

            self.task = kwargs['task']
            self.process = kwargs['process']

    def Url(self, **kwargs):
        self.url_args = kwargs
        return self

    def User(self, user):
        self.user = user

        return self

    def Execute(self, data=None):
        if not data:
            data = {}

        task_finished.connect(self.task_finished)
        flow_do(self.flow_task, self, **data)
        task_finished.disconnect(self.task_finished)
        assert self.task, 'Flow task {} not finished'.format(self.flow_task.name)

        return self

    def Assert(self, assertion):
        fail_message = "Flow task {} post condition fails".format(self.flow_task.name)

        if callable(assertion):
            args = inspect.getargspec(assertion).args

            if args == ['p']:
                assert assertion(self.process), fail_message
            elif args == ['t']:
                assert assertion(self.task), fail_message
            else:
                raise ValueError('Invalid assertion args spec {}'.format(args))
        else:
            assert assertion, fail_message

        return self


class FlowTest(WebTestMixin):
    def __init__(self, flow_cls):
        self.flow_cls = flow_cls

        self.patch_managers = []
        for node in flow_cls._meta.nodes():
            manager = flow_patch_manager(node)
            if manager:
                self.patch_managers.append(manager)

    def Task(self, flow_task):
        return FlowTaskTest(self.app, self.flow_cls, flow_task)

    def __enter__(self):
        self._patch_settings()
        self.renew_app()
        for patch_manager in self.patch_managers:
            patch_manager.__enter__()
        return self

    def __exit__(self, type, value, traceback):
        self._unpatch_settings()
        for patch_manager in self.patch_managers:
            patch_manager.__exit__()