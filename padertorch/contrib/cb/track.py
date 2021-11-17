import contextlib
import typing
import weakref
import time

import torch

__all__ = [
    'track',
    'Tracker',
    'tracker_list',
    # Examples:
    'ShapeTracker',
    'ParameterTracker',
    'TimeTracker',
    'CPUMemTracker',
    'GPUMemTracker',
    'IOPMemTracker',
    'IOPNumTracker',
]


class Tracker:
    def __init__(self, count, depth, leaf, shared_dict):
        """

        Args:
            count: Running index starts with zero
            depth: The call depth
            leaf: Whether this tracker works on a leaf.
                  it might be a real leaf or a module that should be considerd
                  as leaf.
            shared_dict: A dict that is shared between all Trackers.
        """
        self.count = count
        self.depth = depth
        self.leaf = leaf
        self.shared_dict = shared_dict

    def pre(self, module, input) -> None:
        pass

    def post(self, module, input, output) -> None:
        pass


@contextlib.contextmanager
def track(
        net: torch.nn.Module,
        tracker_factory: typing.Callable[[int, int, bool, dict], Tracker],
        leaf_types=tuple(),
):
    """

    Args:
        net:
        tracker_factory:
        leaf_types:

    Returns:

    >>> import psutil, os
    >>> from torch.nn import Sequential, ReLU, Linear
    >>> net = Sequential(Linear(3, 1000), ReLU(), Sequential(Linear(1000, 2), ReLU()))
    >>> net
    Sequential(
      (0): Linear(in_features=3, out_features=1000, bias=True)
      (1): ReLU()
      (2): Sequential(
        (0): Linear(in_features=1000, out_features=2, bias=True)
        (1): ReLU()
      )
    )

    >>> with track(net, ShapeTracker) as trackers:
    ...     _ = net(torch.randn(7, 3))
    >>> for t in trackers:
    ...     print(t)
    0 Sequential          : ([7, 3],) -> [7, 2]
    1   Linear            : ([7, 3],) -> [7, 1000]
    2   ReLU              : ([7, 1000],) -> [7, 1000]
    3   Sequential        : ([7, 1000],) -> [7, 2]
    4     Linear          : ([7, 1000],) -> [7, 2]
    5     ReLU            : ([7, 2],) -> [7, 2]

    >>> with track(net, ParameterTracker) as trackers:
    ...     _ = net(torch.randn(7, 3))
    >>> for t in trackers:
    ...     print(t)
    0 Sequential          : 0
    1   Linear            : 4000
    2   ReLU              : 0
    3   Sequential        : 0
    4     Linear          : 2002
    5     ReLU            : 0
    >>> sum([t.num_params for t in trackers])
    6002


    """
    def register_hook(module, leaf):
        def pre_hook(module, input):
            tracker = tracker_factory(
                len(all_trackers),
                len(tracker_stack),
                leaf,
                shared_dict,
            )
            tracker.pre(module, input)
            tracker_stack.append(tracker)
            all_trackers.append(tracker)

        def hook(module, input, output):
            tracker = tracker_stack.pop()
            tracker.post(module, input, output)

        hooks.append(module.register_forward_pre_hook(pre_hook))
        hooks.append(module.register_forward_hook(hook))

    shared_dict = {}
    all_trackers = []
    tracker_stack = []
    hooks = []

    def apply_filtered(self, fn):
        is_leaf = True
        for module in self.children():
            is_leaf = False
            if module.__class__ in leaf_types:
                fn(module, leaf=True)
            else:
                apply_filtered(module, fn)
        fn(self, leaf=is_leaf)
        return self

    apply_filtered(net, register_hook)

    try:
        yield all_trackers
    finally:
        # remove these hooks
        for h in hooks:
            h.remove()




def tracker_list(*tracker_factories):
    """

    Args:
        *tracker_factories:

    Returns:

    >>> from torch.nn import Sequential, ELU, Linear
    >>> net = Sequential(Linear(3, 1000), ELU(), Sequential(Linear(1000, 2), ELU()))
    >>> with track(net, tracker_list(ShapeTracker, ParameterTracker)) as trackers:
    ...     _ = net(torch.randn(7, 3))
    >>> for ts in zip(*trackers):
    ...     for t in ts:
    ...         print(t)
    ...     print()
    0 Sequential          : ([7, 3],) -> [7, 2]
    1   Linear            : ([7, 3],) -> [7, 1000]
    2   ELU               : ([7, 1000],) -> [7, 1000]
    3   Sequential        : ([7, 1000],) -> [7, 2]
    4     Linear          : ([7, 1000],) -> [7, 2]
    5     ELU             : ([7, 2],) -> [7, 2]
    <BLANKLINE>
    0 Sequential          : 0
    1   Linear            : 4000
    2   ELU               : 0
    3   Sequential        : 0
    4     Linear          : 2002
    5     ELU             : 0
    <BLANKLINE>

    You can run the following in a ipynb with GPU

    import torch
    from torch.nn import Sequential, ELU, Linear
    from padertorch.contrib.cb.track import track, GPUMemTracker, ShapeTracker, tracker_list, ParameterTracker

    net = Sequential(Linear(3, 1000), ELU(), Sequential(Linear(1000, 2), ELU()))
    net.to(torch.device(0))

    with track(net, tracker_list(ShapeTracker, ParameterTracker, GPUMemTracker)) as trackers:
        _ = net(torch.randn(7, 3).to(torch.device(0)))

    for ts in zip(*trackers):
        for t in ts:
            print(t)
        print()

    0 Sequential          : ([7, 3],) -> [7, 2]
    1   Linear            : ([7, 3],) -> [7, 1000]
    2   ELU               : ([7, 1000],) -> [7, 1000]
    3   Sequential        : ([7, 1000],) -> [7, 2]
    4     Linear          : ([7, 1000],) -> [7, 2]
    5     ELU             : ([7, 2],) -> [7, 2]

    0 Sequential          : 0
    1   Linear            : 4000
    2   ELU               : 0
    3   Sequential        : 0
    4     Linear          : 2002
    5     ELU             : 0

    0 Sequential          : 57344 B
    1   Linear            : 28160 B
    2   ELU               : 28160 B
    3   Sequential        : 1024 B
    4     Linear          : 512 B
    5     ELU             : 512 B

    """
    class TrackerList(Tracker):
        def __init__(self, count, depth, leaf, shared_dict):
            self.instances = [
                tf(count, depth, leaf, shared_dict.setdefault(i, {}))
                for i, tf in enumerate(tracker_factories)
            ]

        def pre(self, module, input):
            for i in self.instances:
                i.pre(module, input)

        def post(self, module, input, output):
            for i in self.instances:
                i.post(module, input, output)

        def __getitem__(self, item):
            return self.instances[item]

    return TrackerList


###############################################################################
# Examples                                                                    #
###############################################################################


class ShapeTracker(Tracker):

    def get_shape(self, obj):
        if isinstance(obj, (tuple, list)):
            return obj.__class__(
                filter(None, [self.get_shape(e) for e in obj]))
        elif isinstance(obj, dict):
            return {
                k: shape
                for k, v in obj.items()
                for shape in [self.get_shape(v)]
                if shape is not None
            }
        else:
            try:
                return list(obj.shape)
            except AttributeError:
                return None

    def pre(self, module, input):
        self.module_name = module.__class__.__name__
        self.input_shape = self.get_shape(input)

    def post(self, module, input, output):
        self.output_shape = self.get_shape(output)

    def __repr__(self):
        name = ' ' * self.depth * 2 + self.module_name
        return f'{self.count} {name:20}: ' \
               f'{self.input_shape} -> {self.output_shape}'


class ParameterTracker(Tracker):
    def pre(self, module, input):
        self.module_name = module.__class__.__name__

    def post(self, module, input, output):
        self.num_params = sum([
            p.numel() for p in module.parameters(recurse=self.leaf)])

    def __repr__(self):
        name = ' ' * self.depth * 2 + self.module_name
        return f'{self.count} {name:20}: {self.num_params}'


class TimeTracker(Tracker):
    """

    >>> from torch.nn import Sequential, ReLU, Linear
    >>> net = Sequential(Linear(3, 1000), ReLU(), Sequential(Linear(1000, 2), ReLU()))
    >>> with track(net, TimeTracker) as trackers:
    ...     _ = net(torch.randn(7, 3))
    >>> for t in trackers:  # doctest: +SKIP
    ...      print(t)
    0 Sequential          : 0.00044976104982197285
    1   Linear            : 0.00024844298604875803
    2   ReLU              : 6.943498738110065e-05
    3   Sequential        : 9.508198127150536e-05
    4     Linear          : 5.5750017054378986e-05
    5     ReLU            : 1.9772909581661224e-05
    """
    timestamp = time.perf_counter  # time.process_time

    def pre(self, module, input):
        self.module_name = module.__class__.__name__
        self.start = self.timestamp()

    def post(self, module, input, output):
        self.end = self.timestamp()

    def __repr__(self):
        name = ' ' * self.depth * 2 + self.module_name
        return f'{self.count} {name:20}: {self.end - self.start}'


class CPUMemTracker(Tracker):
    """
    WARNING: This class tracks the memory consumption of the process, not the
             memory of torch.

    >>> from torch.nn import Sequential, ReLU, Linear
    >>> net = Sequential(Linear(3, 1000), ReLU(), Sequential(Linear(1000, 2), ReLU()))
    >>> with track(net, CPUMemTracker) as trackers:
    ...     _ = net(torch.randn(7, 3))
    >>> for t in trackers:  # doctest: +SKIP
    ...      print(t)
    0 Sequential          : 1859584 B
    1   Linear            : 1859584 B
    2   ReLU              : 0 B
    3   Sequential        : 0 B
    4     Linear          : 0 B
    5     ReLU            : 0 B

    """
    def get_mem(self):
        # return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        import psutil, os
        return psutil.Process(os.getpid()).memory_info().rss

    def pre(self, module, input):
        self.module_name = module.__class__.__name__
        self.pre_mem = self.get_mem()

    def post(self, module, input, output):
        self.post_mem = self.get_mem()

    def __repr__(self):
        name = ' ' * self.depth * 2 + self.module_name
        return f'{self.count} {name:20}: {self.post_mem - self.pre_mem} B'


class GPUMemTracker(Tracker):
    """

    Doctest runs on CPU, hence the GPU memory tracking is boring

    >>> from torch.nn import Sequential, ReLU, Linear
    >>> net = Sequential(Linear(3, 1000), ReLU(), Sequential(Linear(1000, 2), ReLU()))
    >>> with track(net, GPUMemTracker) as trackers:
    ...     _ = net(torch.randn(7, 3))
    >>> for t in trackers:
    ...      print(t)
    0 Sequential          : 0 B
    1   Linear            : 0 B
    2   ReLU              : 0 B
    3   Sequential        : 0 B
    4     Linear          : 0 B
    5     ReLU            : 0 B

    """
    device = 0  # Use export CUDA_VISIBLE_DEVICES=1 to switch device

    def get_mem(self):
        return torch.cuda.memory_allocated(device=self.device)

    def pre(self, module, input):
        self.module_name = module.__class__.__name__
        self.pre_mem = self.get_mem()

    def post(self, module, input, output):
        self.post_mem = self.get_mem()

    def __repr__(self):
        name = ' ' * self.depth * 2 + self.module_name
        return f'{self.count} {name:20}: {self.post_mem - self.pre_mem} B'


class IOPNumTracker(Tracker):
    """
    Input Output Parameter Number of element Tracker.

    Tracks the number of values in the input (I) and output (O) and also the
    number of parameters (P) each layer has. Further it tracks, if the values
    need a gradient.
    The method `total_repr` can be used on any instance to print the total
    number.
    Note: For `total_repr` considers, that some tensors apear multiple times
          (e.g. as input and output), so the total numbers are smaller than
          the sum of the individual numbers.

    >>> from torch.nn import Sequential, ReLU, Linear
    >>> net = Sequential(Linear(3, 1000), ReLU(), Sequential(Linear(1000, 2), ReLU()))
    >>> with track(net, IOPNumTracker) as trackers:
    ...     _ = net(torch.randn(7, 3))
    >>> for t in trackers:
    ...      print(t)
    0 Sequential          : P:      0 (requires_grad:      0) IO:     35 (requires_grad:     14)
    1   Linear            : P:   4000 (requires_grad:   4000) IO:   7021 (requires_grad:   7000)
    2   ReLU              : P:      0 (requires_grad:      0) IO:  14000 (requires_grad:  14000)
    3   Sequential        : P:      0 (requires_grad:      0) IO:   7014 (requires_grad:   7014)
    4     Linear          : P:   2002 (requires_grad:   2002) IO:   7014 (requires_grad:   7014)
    5     ReLU            : P:      0 (requires_grad:      0) IO:     28 (requires_grad:     28)
    >>> print(t.total_repr())
    P:   6002 (requires_grad:   6002) IO:  14049 (requires_grad:  14028)
    """
    local_dict = None

    def flat_tensors(self, obj):
        if isinstance(obj, (tuple, list)):
            for o in obj:
                yield from self.flat_tensors(o)
        elif isinstance(obj, dict):
            for v in obj.values():
                yield from self.flat_tensors(v)
        else:
            if isinstance(obj, torch.Tensor):
                yield obj

    def maybe_init(self):
        import weakref
        if len(self.shared_dict) == 0:
            self.shared_dict['parameters_learnable'] = 0
            self.shared_dict['parameters_fixed'] = 0
            self.shared_dict['tensors_learnable'] = 0
            self.shared_dict['tensors_fixed'] = 0
            self.shared_dict['visited'] = _IDBasedWeakSet()

        if not self.local_dict:
            self.local_dict = {}
            self.local_dict['parameters_learnable'] = 0
            self.local_dict['parameters_fixed'] = 0
            self.local_dict['tensors_learnable'] = 0
            self.local_dict['tensors_fixed'] = 0
            self.local_dict['visited'] = _IDBasedWeakSet()

    def get_size(self, tensor):
        return tensor.numel()

    def maybe_add(self, tensor, learnable_key, fixed_key):

        if tensor in self.shared_dict['visited']:
            pass
        else:
            self.shared_dict['visited'].add(tensor)
            if tensor.requires_grad:
                self.shared_dict[learnable_key] += self.get_size(tensor)
            else:
                self.shared_dict[fixed_key] += self.get_size(tensor)

        if tensor not in self.local_dict['visited']:
            self.local_dict['visited'].add(tensor)
            if tensor.requires_grad:
                self.local_dict[learnable_key] += self.get_size(tensor)
            else:
                self.local_dict[fixed_key] += self.get_size(tensor)

    def pre(self, module, input):

        self.module_name = module.__class__.__name__
        self.maybe_init()

        for p in module.parameters(recurse=self.leaf):
            self.maybe_add(p, 'parameters_learnable', 'parameters_fixed')

        for t in self.flat_tensors(input):
            self.maybe_add(t, 'tensors_learnable', 'tensors_fixed')

    def post(self, module, input, output):
        for t in self.flat_tensors(output):
            self.maybe_add(t, 'tensors_learnable', 'tensors_fixed')

    def _to_str(self, value):
        return f'{value:6}'

    def __repr__(self):
        name = ' ' * self.depth * 2 + self.module_name
        l = self.local_dict
        pl = l['parameters_learnable']
        pf = l['parameters_fixed']
        tl = l['tensors_learnable']
        tf = l['tensors_fixed']
        return f'{self.count} {name:20}: ' \
               f'P: {self._to_str(pl+pf)} (requires_grad: {self._to_str(pl)}) ' \
               f'IO: {self._to_str(tl+tf)} (requires_grad: {self._to_str(tl)})'

    def total_repr(self):
        l = self.shared_dict
        pl = l['parameters_learnable']
        pf = l['parameters_fixed']
        tl = l['tensors_learnable']
        tf = l['tensors_fixed']
        return f'P: {self._to_str(pl+pf)} (requires_grad: {self._to_str(pl)}) ' \
               f'IO: {self._to_str(tl+tf)} (requires_grad: {self._to_str(tl)})'


class IOPMemTracker(IOPNumTracker):
    """

    >>> from torch.nn import Sequential, ReLU, Linear
    >>> net = Sequential(Linear(3, 1000), ReLU(), Sequential(Linear(1000, 2), ReLU()))
    >>> with track(net, IOPMemTracker) as trackers:
    ...     _ = net(torch.randn(7, 3))
    >>> for t in trackers:
    ...      print(t)
    0 Sequential          : P:      0 B (requires_grad:      0 B) IO:    140 B (requires_grad:     56 B)
    1   Linear            : P:  16000 B (requires_grad:  16000 B) IO:  28084 B (requires_grad:  28000 B)
    2   ReLU              : P:      0 B (requires_grad:      0 B) IO:  56000 B (requires_grad:  56000 B)
    3   Sequential        : P:      0 B (requires_grad:      0 B) IO:  28056 B (requires_grad:  28056 B)
    4     Linear          : P:   8008 B (requires_grad:   8008 B) IO:  28056 B (requires_grad:  28056 B)
    5     ReLU            : P:      0 B (requires_grad:      0 B) IO:    112 B (requires_grad:    112 B)
    >>> print(t.total_repr())
    P:  24008 B (requires_grad:  24008 B) IO:  56196 B (requires_grad:  56112 B)
    """
    def get_size(self, tensor):
        return tensor.nelement() * tensor.element_size()

    def _to_str(self, value):
        return f'{value:6} B'


class _IDBasedWeakSet:
    """
    >>> a = torch.tensor([1, 2])
    >>> b = torch.tensor([1, 2, 3])
    >>> s = _IDBasedWeakSet()
    >>> a in s, b in s
    (False, False)
    >>> s.add(a)
    >>> a in s, b in s
    (True, False)
    >>> s.add(b)
    >>> a in s, b in s
    (True, True)
    >>> s
    _IDBasedWeakSet({tensor([1, 2]), tensor([1, 2, 3])})
    >>> del a
    >>> s
    _IDBasedWeakSet({tensor([1, 2, 3])})
    """
    def __init__(self, items=None):
        self.data = {}
        if items:
            for i in items:
                self.add(i)

    def add(self, item):
        if item is None:
            raise ValueError(item)
        self.data[id(item)] = weakref.ref(item)

    def __contains__(self, item):
        if id(item) in self.data:
            ref = self.data[id(item)]()
            if ref is None:
                return False  # object was deleted
            else:
                return ref is item
        else:
            return False

    def __repr__(self):
        if self.data:
            s = [v() for v in self.data.values()]
            s = [repr(v) for v in s if v is not None]
            s = ', '.join(s)
            return f'{self.__class__.__name__}({{{s}}})'
        else:
            return f'{self.__class__.__name__}()'
