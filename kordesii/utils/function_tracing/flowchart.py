﻿"""
Module: flowchart.py

Created: 3 May 18

Description:
    This module uses the idaapi.FlowChart object and extends it as well as idaapi.BasicBlock in order to add 
    functionality including Breadth-First and Depth-First chart traversal, locating a specific block within the
    chart based on an EA, generating a list of all possible paths to a specified EA, etc.
"""

# Python import
from operator import attrgetter
from copy import copy, deepcopy
import collections

# IDA Python imports
import idaapi
import idautils
import idc

# Import CPU tracing capabilities
from .cpu_context import ProcessorContext
from . import cpu_emulator as processor

# Start up the processor
processor.setup()


class PathBlock(object):
    """
    Represents a linked-list of objects constituting a path from a specific node to the function entry point node.  This
    object can also track cpu context up to a certain EA.
    """
    def __init__(self, bb, prev):
        self.bb = bb
        self.prev = prev
        self._context = None
        self._context_ea = None  # ea that the context has been filled to (but not including)

    def __contains__(self, ea):
        return ea in self.bb

    def cpu_context(self, ea=None):
        """
        Returns the cpu context filled to (and including) the specified ea.

        :param int ea: address of interest (defaults to the last ea of the block)

        :return cpu_context.ProcessorContext: cpu context
        """
        if ea is not None and not (self.bb.start_ea <= ea < self.bb.end_ea):
            raise KeyError("Provided address 0x{:X} not in this block "
                           "(0x{:X} :: 0x{:X})".format(ea, self.bb.start_ea, self.bb.end_ea))

        # Determine address to stop computing.
        if ea is None:
            end = self.bb.end_ea
        else:
            end = idc.next_head(ea)

        assert end is not None
        # Fill context up to requested endpoint.
        if self._context_ea != end:
            # Create context if not created or current context goes past requested ea.
            if not self._context or self._context_ea > end:
                # Need to check if there is a prev, if not, then we need to create a default context here...
                if self.prev:
                    self._context = self.prev.cpu_context()
                else:
                    self._context = ProcessorContext()

                self._context_ea = self.bb.start_ea

            # Fill context up to requested ea.
            for ip in idautils.Heads(self._context_ea, end):
                processor.execute(self._context, ip)

            self._context_ea = end

        return deepcopy(self._context)
         
        
def get_flowchart(ea):
    """
    Helper function to obtain an idaapi.FlowChart object for a given ea.

    :param int ea: ea of interest

    :return idaapi.FlowChart: idaapi.FlowChart object
    """
    func = idaapi.get_func(ea)
    flowchart_ = idaapi.FlowChart(func)
    return flowchart_


def get_codeblock(ea):
    """
    Helper function to obtain a idaapi.BasicBlock object containing a given ea.

    :param int ea: ea of interest

    :return idaapi.BasicBlock: idaapi.BasicBlock object
    """
    flowchart_ = get_flowchart(ea)
    for code_block in flowchart_:
        if code_block.start_ea <= ea < code_block.end_ea:
            return code_block


class CustomBasicBlock(idaapi.BasicBlock):
    """
    An idaapi.BasicBlock object which has been extended with additional functionality beyond the base class.

    Additional functionality:
        - Iterate all the child BasicBlocks by calling next
        - Iterate all the parent BasicBlocks using prev
        - Ability to use BasicBlocks as hashable objects (ie: as dictionary keys)
        - Check if two BasicBlocks are equal (based on their start_ea)
        - Check if an EA is contained in a BasicBlock (ie: if ea in <CustomBasicBlock>:)
    """
    def __init__(self, id_ea, bb=None, fc=None):
        if bb is None and fc is None:
            temp_codeblock = get_codeblock(id_ea)
            self.__dict__.update(temp_codeblock.__dict__)
        else:
            super(CustomBasicBlock, self).__init__(id=id_ea, bb=bb, fc=fc)

    @property
    def next(self):
        return self.succs()

    @property
    def prev(self):
        return self.preds()

    def __hash__(self):
        return self.start_ea

    def __repr__(self):
        return "<CustomBasicBlock(start_ea=0x{:08X}, end_ea=0x{:08X})>".format(self.start_ea, self.end_ea)

    def __eq__(self, other):
        return self.start_ea == other.start_ea

    def __contains__(self, ea):
        return self.start_ea <= ea < self.end_ea


class FlowChart(idaapi.FlowChart):
    """
    Object containing the function graph generated by IDA.  Implements the traversal of the function.
    """
    def __init__(self, f, bounds=None, flags=idaapi.FC_PREDS, node_type=idaapi.BasicBlock):
        self.f = idaapi.get_func(f)
        self.node_type = node_type
        super(FlowChart, self).__init__(f=self.f, bounds=bounds, flags=flags)
        self._path_cache = collections.defaultdict(list)
        self._gen_cache = {}

    def _dfs(self, start_ea=None):
        """
        Blind depth-first traversal of the graph.  For each block, obtain the children (or blocks which are reachable
        from the current block), sort the children by their start_ea in ascending order, and "push" the list on to the
        front of the non_visisted blocks list.

        :yield idaapi.BasicBlock: idaapi.BasicBlock object
        """
        # Set our flag to True if start_ea is none so we yield all blocks, else wait till we find the requested block
        block_found = start_ea is None
        non_visited = [self[0]]
        visited = set()
        while non_visited:
            cur_block = non_visited.pop(0)
            if cur_block.start_ea in visited:
                continue

            visited.add(cur_block.start_ea)
            non_visited[0:0] = sorted(cur_block.succs(), key=attrgetter("start_ea"))
            if not block_found:
                block_found = cur_block.start_ea <= start_ea < cur_block.end_ea

            if block_found:
                yield cur_block

    def _dfs_reverse(self, start_ea=None):
        """
        Perform a reverse traversal of the graph in depth-first manner where given a start node, traverse 1 complete
        path to the root node before following additional paths.

        :param int start_ea: EA within a block from which to start traversing

        :yield: block object
        """
        if start_ea:
            non_visited = [self.find_block(start_ea)]
        else:
            non_visited = list(sorted(self, key=attrgetter("start_ea"), reverse=True))[-1:]

        while non_visited:
            cur_block = non_visited.pop(0)
            # Prevent loops by making sure the start_ea for all preds is less than the current block's
            non_visited[0:0] = [pred for pred in sorted(cur_block.preds(), 
                                                        key=attrgetter("start_ea"), 
                                                        reverse=True) if pred.start_ea < cur_block.start_ea]
            yield cur_block

    def dfs_iter_blocks(self, start_ea=None, reverse=False):
        """
        Iterate over idaapi.BasicBlocks in depth-first manner.

        >>> ea = 0x1001234  # some EA within a function
        >>> fc = FlowChart(ea)
        >>> for block in fc.dfs_iter_blocks():
        >>>     print ">>> Block: 0x{:x} - 0x{:x}".format(block.start_ea, block.end_ea)

        :param int start_ea: optional address to start iterating from.

        :param bool reverse: iterate in reverse
        """
        if reverse:
            for cur_block in self._dfs_reverse(start_ea):
                yield cur_block

        else:
            for cur_block in self._dfs(start_ea):
                yield cur_block

    def dfs_iter_heads(self, start_ea=None, reverse=False):
        """
        Iterate over instructions in idaapi.BasicBlocks in depth-first manner.

        >>> ea = 0x1001234  # some EA within a function
        >>> fc = FlowChart(ea)
        >>> for head_ea in fc.dfs_iter_heads():
        >>>     print ">>> 0x{:x}: {}".format(head_ea, idc.generate_disasm_line(head_ea, 0))

        :param int start_ea: option address to start iterating from.

        :param bool reverse: iterate in reverse
        """
        _first_block = True
        for cur_block in self.dfs_iter_blocks(start_ea, reverse):
            if reverse:
                if start_ea and _first_block:
                    ea = start_ea
                else:
                    ea = cur_block.end_ea

                heads = reversed(list(idautils.Heads(cur_block.start_ea, ea)))

            else:
                if start_ea and _first_block:
                    ea = start_ea
                else:
                    ea = cur_block.start_ea

                heads = idautils.Heads(ea, cur_block.end_ea)

            _first_block = False

            for head in heads:
                yield head

    def _bfs(self, start_ea=None):
        """
        Blind breadth-first traversal of graph.  For each block, obtain the children (or blocks which are reacable
        from the current block), sorth the children by their start_ea in ascending order, and append the list to the
        end of the non_visited blocks list.

        :yield idaapi.BasicBlock: idaapi.BasicBlock
        """
        # If no start_ea is provided, then display all blocks, so set our flag as True, otherwise wait till we find
        # the required block befor yielding
        block_found = start_ea is None
        non_visited = [self[0]]
        visited = set()
        while non_visited:
            cur_block = non_visited.pop(0)
            if cur_block.start_ea in visited:
                continue

            visited.add(cur_block.start_ea)
            non_visited.extend(sorted(cur_block.succs(), key=attrgetter("start_ea")))
            if not block_found:
                block_found = cur_block.start_ea <= start_ea < cur_block.end_ea

            if block_found:
                yield cur_block

    def _bfs_reverse(self, start_ea=None):
        """
        Perform a reverse traversal of the graph in breadth-first manner.

        :param int start_ea: EA within a block from which to start traversing

        :yield idaapi.BasicBlocks: idaapi.BasicBlocks
        """
        if start_ea:
            non_visited = [self.find_block(start_ea)]
        else:
            non_visited = list(sorted(self, key=attrgetter("start_ea"), reverse=True))[-1:]

        while non_visited:
            cur_block = non_visited.pop(0)
            # Prevent loops by making sure the start_ea for all preds is less than the current block's
            non_visited.extend([pred for pred in sorted(cur_block.preds(),
                                                        key=attrgetter("start_ea"),
                                                        reverse=True) if pred.start_ea < cur_block.start_ea])
            yield cur_block

    def bfs_iter_blocks(self, start_ea=None, reverse=False):
        """
        Iterate over idaapi.BasicBlocks in breadth-first manner.

        >>> ea = 0x1001234  # some EA within a function
        >>> fc = FlowChart(ea)
        >>> for block in fc.bfs_iter_blocks():
        >>>     print ">>> Block: 0x{:x} - 0x{:x}".format(block.start_ea, block.end_ea)

        :param int start_ea: optional address to start iterating from

        :param bool reverse: iterate in reverse
        """
        if reverse:
            for cur_block in self._bfs_reverse(start_ea):
                yield cur_block

        else:
            for cur_block in self._bfs(start_ea):
                yield cur_block

    def bfs_iter_heads(self, start_ea=None, reverse=False):
        """
        Iterate over instructions in idaapi.BasicBlocks in breadth-first manner.

        >>> ea = 0x1001234  # some EA within a function
        >>> fc = FlowChart(ea)
        >>> for head_ea in fc.bfs_iter_heads():
        >>>     print ">>> 0x{:x}: {}".format(head_ea, idc.generate_disasm_line(head_ea, 0))

        :param int start_ea: optional address to start iterating from.

        :param bool reverse: iterate in reverse
        """
        _first_block = True
        for cur_block in self.bfs_iter_blocks(start_ea, reverse):
            if reverse:
                if start_ea and _first_block:
                    ea = start_ea
                else:
                    ea = cur_block.end_ea

                heads = reversed(list(idautils.Heads(cur_block.start_ea, ea)))

            else:
                if start_ea and _first_block:
                    ea = start_ea
                else:
                    ea = cur_block.start_ea

                heads = idautils.Heads(ea, cur_block.end_ea)

            _first_block = False

            for head in heads:
                yield head

    def find_block(self, ea):
        """
        Locate a BasicBlock which contains the specified ea

        >>> ea = 0x1001234  # some EA within a function
        >>> fc = FlowChart(ea)
        >>> block = fc.find_block(ea)
        >>> print ">>> Block: 0x{:x} - 0x{:x}".format(block.start_ea, block.end_ea)

        :param int ea: ea of interest

        :return: BasicBlock object
        """
        for block in self:
            if block.start_ea <= ea < block.end_ea:
                return block

    def _paths_to_ea(self, ea, cur_block, visited=None, cur_path=None):
        """
        Recursive DFS traversal of graph which yields a path to EA.

        :param int ea: ea of interesting

        :param idaapi.BasicBlock cur_block: current block in graph

        :param set visited: set of blocks already visited

        :param list cur_path: a list of blocks on the current path

        :yield list: current path
        """
        cur_path = cur_path or []

        # Initialize our visted set of blocks
        if visited is None:
            visited = set()

        # Mark the current block as visited and add it to the current path
        visited.add(cur_block.start_ea)
        cur_path.append(cur_block)
        # We've found our block, so yield the current path
        if cur_block.start_ea <= ea < cur_block.end_ea:
            yield copy(cur_path)

        # Continue traversing
        for block in cur_block.succs():
            if block.start_ea in visited:
                continue

            for path in self._paths_to_ea(ea, block, visited, cur_path):
                yield path

        # Remove the current block from the path and visited so it is included in subsequent paths
        cur_path.pop()
        visited.remove(cur_block.start_ea)

    def paths_to_ea(self, ea):
        """
        Yield a list which contains all the blocks on a path from the function entry point to the block 
        containing the specified ea.  Raises ValueError if specified EA is not within the current function.

        :param int ea: ea of interest

        :yield list: list of BasicBlocks residing on a given path to EA
        """
        # make sure the specified ea is within the function
        if not (self.f.start_ea <= ea < self.f.end_ea):
            raise ValueError

        for path in self._paths_to_ea(ea, self[0]):
            yield path

    def _build_path(self, cur_block, visited=None):
        """
        Yields a Path object based on an EA for a given block.

        :param idaapi.BasicBlock block: A BasicBlock object

        :yield: path object
        """
        if visited is None:
            visited = set()

        cb_start_ea = cur_block.start_ea
        # Add our current block to visited blocks
        visited.add(cb_start_ea)

        # Check to make sure this path isn't already cached, and build it if it isn't
        path_cache = self._path_cache.get(cb_start_ea, [])
        # path_cache has a default item type of list, so we need to check the length, not for None
        if not len(path_cache):
            # Our terminating condition is actually when we have no more parent blocks to traverse
            cb_parents = list(cur_block.preds())
            if not len(cb_parents):
                p_block = PathBlock(cur_block, None)
                yield p_block
            else:
                # Continue creating blocks and updating the parents
                for block in cb_parents:
                    blk_start_ea = block.start_ea
                    # Should we consider a block with an EA AFTER our current block as a parent?  This indicates
                    # a loop and may/may not put us in a very strange situation where we are building for paths
                    # that are completely irrelevant for the path we are asking for....
                    if blk_start_ea in visited or blk_start_ea > cb_start_ea:
                        continue

                    # Get all the paths to the current parent block
                    for _p_block in self._build_path(block, visited):
                        p_block = PathBlock(cur_block, _p_block)
                        yield p_block

        # We have a cache hit so yield all the blocks in it
        else:
            for blk in path_cache:
                yield blk

        # Remove the current block for the visited set
        visited.remove(cb_start_ea)

    def get_paths(self, ea):
        """
        Given an EA, iterate over the paths to the EA.

        For usage example, see function_tracer.trace in function_tracer.py

        WARNING:
        DO NOT WRAP THIS GENERATOR IN list()!!!  This generator will itereate all possible paths to the node containing
        the specified EA.  On functions containing large numbers of jumps, the number of paths grows exponentially and
        you WILL hit memory exhaustion limits, extremely slow run times, etc.  Use extremely conservative constraints
        when iterating.  Nodes containing up to at least 32,768 paths are computed in a reasonably sane amount of time,
        though it probably doesn't make much sense to check this many paths for the data you are looking for.

        :param int ea: EA of interest

        :yield: a path to the object
        """
        # Obtain the block containing the EA of interest
        block = self.find_block(ea)
        # Obtain paths that have currently been built
        cached_paths = self._path_cache[block.start_ea]
        # Yield any paths already constructed
        for path in cached_paths:
            yield path

        # If we are still traversing paths at this point, pull the generator (if it exists), or create one
        path_generator = self._gen_cache.get(block.start_ea)
        if not path_generator:
            path_generator = self._build_path(block)
            self._gen_cache[block.start_ea] = path_generator

        # Iterate the paths created by the generator
        for path in path_generator:
            self._path_cache[path.bb.start_ea].append(path)
            yield path

    def _getitem(self, index):
        """
        Override the idaapi.FlowChart._getitem function to return the appropriate object type.
        """
        return self.node_type(index, self._q[index], self)