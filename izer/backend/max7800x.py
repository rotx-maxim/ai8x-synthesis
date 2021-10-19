###################################################################################################
# Copyright (C) Maxim Integrated Products, Inc. All Rights Reserved.
#
# Maxim Integrated Products, Inc. Default Copyright Notice:
# https://www.maximintegrated.com/en/aboutus/legal/copyrights.html
###################################################################################################
"""
Backend for MAX7800X embedded code generation and RTL simulations
"""
import copy
import hashlib
import os
import sys

import numpy as np

from izer import apbaccess, assets, compute, kbias, kernels, load, op, rtlsim, state, stats
from izer import tornadocnn as tc
from izer.eprint import eprint, nprint, wprint
from izer.simulate import (conv1d_layer, conv2d_layer, convtranspose2d_layer, eltwise_layer,
                           passthrough_layer, pooling_layer, print_data, show_data)
from izer.utils import ffs, fls, overlap, popcount

from . import backend


class Backend(backend.Backend):
    """
    Backend for MAX7800X CNN network code generation
    """

    def create_net(self) -> str:  # pylint: disable=too-many-locals,too-many-branches,no-self-use
        """
        Chain multiple CNN layers, create and save input and output
        """
        # Cache variables locally for faster access
        activation = state.activation
        allow_streaming = state.allow_streaming
        apb_base = state.apb_base
        api_filename = state.api_filename
        avg_pool_rounding = state.avg_pool_rounding
        base_directory = state.base_directory
        bias = state.bias
        bias_group_map = state.bias_group_map
        big_data = state.big_data
        block_mode = state.block_mode
        board_name = state.board_name
        bypass = state.bypass
        c_filename = state.c_filename
        calcx4 = state.calcx4
        compact_data = state.compact_data
        conv_groups = state.conv_groups
        data = state.data
        debug_new_streaming = state.debug_new_streaming
        debug_snoop = state.debug_snoop
        dilation = state.dilation
        eltwise = state.eltwise
        embedded_code = state.embedded_code
        ext_rdy = state.ext_rdy
        fast_fifo = state.fast_fifo
        fast_fifo_quad = state.fast_fifo_quad
        fifo = state.fifo
        final_layer = state.final_layer
        first_layer_used = state.first_layer_used
        flatten = state.flatten
        forever = state.forever
        ignore_bias_groups = state.ignore_bias_groups
        in_offset = state.in_offset
        in_sequences = state.in_sequences
        increase_delta1 = state.increase_delta1
        increase_delta2 = state.increase_delta2
        increase_start = state.increase_start
        init_tram = state.init_tram
        input_chan = state.input_channels
        input_channel_skip = state.input_channel_skip
        input_csv = state.input_csv
        input_dim = state.input_dim
        input_skip = state.input_skip
        kernel = state.weights
        kernel_size = state.kernel_size
        layers = state.layers
        legacy_test = state.legacy_test
        link_layer = state.link_layer
        log = state.log
        log_filename = state.log_filename
        log_intermediate = state.log_intermediate
        log_pooling = state.log_pooling
        measure_energy = state.measure_energy
        next_sequence = state.next_sequence
        no_error_stop = state.no_error_stop
        oneshot = state.oneshot
        operands = state.operands
        operator = state.operator
        out_offset = state.out_offset
        output_chan = state.output_channels
        output_dim = state.output_dim
        output_filename = state.output_filename
        output_padding = state.output_padding
        output_processor_map = state.output_processor_map
        output_shift = state.output_shift
        output_width = state.output_width
        override_delta1 = state.override_delta1
        override_delta2 = state.override_delta2
        override_rollover = state.override_rollover
        override_start = state.override_start
        overwrite = state.overwrite
        overwrite_ok = state.overwrite_ok
        padding = state.padding
        pool = state.pool
        pool_average = state.pool_average
        pool_dilation = state.pool_dilation
        pool_first = state.pool_first
        pool_stride = state.pool_stride
        pooled_dim = state.pooled_dim
        powerdown = state.powerdown
        prefix = state.prefix
        pretend_zero_sram = state.pretend_zero_sram
        prev_sequence = state.prev_sequence
        processor_map = state.processor_map
        quantization = state.quantization
        rd_ahead = state.read_ahead
        repeat_layers = state.repeat_layers
        reshape_inputs = state.reshape_inputs
        riscv = state.riscv
        riscv_cache = state.riscv_cache
        riscv_flash = state.riscv_flash
        simple1b = state.simple1b
        simulated_sequence = state.simulated_sequence
        snoop = state.snoop
        snoop_sequence = state.snoop_sequence
        start_layer = state.start_layer
        stopstart = state.stopstart
        streaming = state.streaming
        stride = state.stride
        tcalc = state.tcalc
        timeout = state.timeout
        timer = state.timer
        verbose = state.verbose
        verify_kernels = state.verify_kernels
        verify_writes = state.verify_writes
        weight_filename = state.weight_filename
        write_gap = state.write_gap
        write_zero_regs = state.write_zero_regs
        zero_sram = state.zero_sram
        zero_unused = state.zero_unused

        if not os.path.isdir('assets'):
            eprint('The assets folder is missing from the current directory.')

        assert tc.dev is not None
        device = tc.dev.device

        in_expand = [0] * layers
        in_expand_invol = [0] * layers
        out_expand = [0] * layers
        in_expand_thresh = [0] * layers
        out_expand_thresh = [0] * layers
        tram_max = [0] * layers
        hw_padding = padding.copy()

        input_dim_str = [None] * layers
        output_dim_str = [None] * layers
        kernel_size_str = [None] * layers
        pool_str = [None] * layers
        padding_str = [None] * layers
        pool_stride_str = [None] * layers
        pool_dilation_str = [None] * layers
        dilation_str = [None] * layers
        stride_str = [None] * layers
        stream_buf = [None] * layers

        out_ignore = [0] * layers
        out_pad = [0] * layers

        terminating_layer = final_layer
        for i, s in enumerate(simulated_sequence):
            if s == -1:
                terminating_layer = i
                break

        if zero_sram:
            state.rtl_preload = False

        if start_layer > 0 and not tc.dev.SUPPORT_LINK_LAYER:
            eprint("`--start-layer` is not supported on this device.")

        if start_layer > tc.dev.MAX_START_LAYER:
            eprint(f"`--start-layer` is set to {start_layer}, but the device only supports "
                   f"a maximum of {tc.dev.MAX_START_LAYER}.")

        if link_layer and not tc.dev.SUPPORT_LINK_LAYER:
            eprint("`--link-layer` is not supported on this device.")

        if any(rd_ahead) and not tc.dev.SUPPORT_READ_AHEAD:
            eprint("`readahead` is not supported on this device.")

        if any(calcx4) and not tc.dev.SUPPORT_CALCX4:
            eprint("`calcx4` is not supported on this device.")

        if state.pipeline and not tc.dev.SUPPORT_PIPELINE:
            eprint("`--pipeline` is not supported on this device.")

        if state.pll and not tc.dev.SUPPORT_PLL:
            eprint("`--pll` is not supported on this device.")

        if state.fifo_go and not tc.dev.SUPPORT_FIFO_GO:
            eprint("`--fifo-go` is not supported on this device.")

        if snoop is not None and not tc.dev.SUPPORT_SNOOP:
            eprint("`snoop` is not supported on this device.")

        if oneshot and not tc.dev.SUPPORT_ONESHOT:
            eprint("`--one-shot` is not supported on this device.")

        if state.pipeline is None:
            state.pipeline = tc.dev.SUPPORT_PIPELINE
        pipeline = state.pipeline  # Cache

        if state.pll is None:
            state.pll = pipeline

        if zero_sram or pretend_zero_sram:
            # Clear every seventh kernel so we can test the BIST
            for i, _ in enumerate(kernel):
                kernel[i][::7] = np.full(shape=kernel[i][0].shape, fill_value=0, dtype=np.int64)

        if state.result_output and (state.mlator or oneshot or stopstart):
            state.result_output = False
        result_output = state.result_output  # Cache

        if result_output:
            state.max_count = None

        if state.mexpress and any(calcx4):
            wprint('Ignoring --mexpress since calcx4 is used.')  # FIXME
            state.mexpress = False
        mexpress = state.mexpress

        if mexpress:
            state.compact_weights = True
        compact_weights = state.compact_weights

        # Check streaming and FIFO constraints
        fifo_group = fast_fifo

        if not fifo and state.synthesize_input is not None:
            eprint('`--synthesize-input` requires `--fifo`')
        if big_data[start_layer] and state.synthesize_input is not None:
            eprint('`--synthesize-input` requires `data_format: HWC`')

        if fifo:
            if start_layer != 0:
                eprint('`--start_layer` must be 0 when using a FIFO.')

            if input_chan[start_layer] > 16 \
               or big_data[start_layer] and input_chan[start_layer] > 4:
                eprint("Using the FIFO is restricted to a maximum of 4 input channels (CHW) or "
                       f"16 channels (HWC); this test is using {input_chan[start_layer]} "
                       "channels.")
            if big_data[start_layer] and processor_map[start_layer] & ~0x0001000100010001 != 0 \
               or not big_data[start_layer] \
               and processor_map[start_layer] & ~0x000f000f000f000f != 0:
                eprint("The FIFO is restricted to processors 0, 16, 32, 48 (CHW) or "
                       "0-3, 16-19, 32-35, 48-51 (HWC).")
            if fast_fifo:
                if big_data[start_layer] and input_chan[start_layer] > 1:
                    eprint("Fast FIFO supports only a single CHW input channel; "
                           f"this test is using {input_chan[start_layer]} channels.")
                elif not big_data[start_layer] and input_chan[start_layer] > 4:
                    eprint("Fast FIFO supports up to four HWC input channels; "
                           f"this test is using {input_chan[start_layer]} channels.")
                if processor_map[start_layer] != 1 and processor_map[start_layer] & 0x0e == 0:
                    fifo_group = False
                if output_width[start_layer] != 8:
                    eprint('Single-layer fast FIFO setup requires output width of 8.')
                if operator[start_layer] == op.NONE:
                    eprint('Fast FIFO requires a convolution operation in the first layer.')
        elif streaming[start_layer] and not allow_streaming:
            eprint('Streaming in the first layer requires use of a FIFO.')
        if any(streaming) and start_layer != 0:
            eprint('`--start_layer` must be 0 when using streaming.')

        for ll in range(min(tc.dev.MAX_STREAM_LAYERS, layers)):
            if next_sequence[ll] != -1 and next_sequence[ll] != ll + 1 and streaming[ll]:
                eprint(f'`next_sequence` must be {ll+1} when using streaming in layer {ll}. '
                       f'Currently configured: {next_sequence[ll]}')

            if tc.dev.EMULATE_1X1_STREAMING and streaming[ll] and kernel_size[ll] == [1, 1] \
               and operator[ll] in [op.CONV2D, op.CONVTRANSPOSE2D]:
                nprint(f'Layer {ll}: Using 3x3 kernel hardware for 1x1 streaming layer.')
                # Create 3x3 weights from 1x1 weights and emulate using 3x3 kernels
                weight33 = np.zeros((kernel[ll].shape[0], 3, 3), dtype=np.int64)
                weight33[:, 1, 1] = kernel[ll][:, 0, 0]
                kernel[ll] = weight33
                assert padding[ll] == [0, 0]
                padding[ll] = [1, 1]
                hw_padding[ll] = [1, 1]
                kernel_size[ll][0] = kernel_size[ll][1] = 3

            if not tc.dev.SUPPORT_STREAM_NONPAD_FINAL and streaming[ll] \
               and (next_sequence[ll] == -1 or not streaming[next_sequence[ll]]) \
               and (padding[ll][0] == 0 or padding[ll][1] == 0):
                eprint(f'Padding for the final streaming layer (layer {ll}) must not be zero.')

        if state.mlator and (output_dim[terminating_layer][0]
                             * output_dim[terminating_layer][1] < 4
                             or output_width[terminating_layer] > 8):
            wprint('--mlator should only be used with 4 or more 8-bit outputs per channel; '
                   'ignoring.')
            state.mlator = False
        mlator = state.mlator

        if state.softmax and output_width[terminating_layer] == 8:
            wprint('--softmax should only be used with `output_width: 32`.')

        if fast_fifo and not riscv:
            eprint('--fast-fifo requires --riscv')

        if state.sleep and not riscv:
            eprint('--deepsleep requires --riscv')

        if oneshot and timer is not None:
            eprint('--timer is not supported when using --one-shot')

        if not tc.dev.SUPPORT_KERNEL_BYPASS \
           and any(bypass[ll] for ll in range(first_layer_used, layers)):
            eprint('Kernel bypass is not supported on this device.')

        processor_map_0 = processor_map[start_layer]
        if fast_fifo_quad:
            processor_map[start_layer] = processor_map_0 << 48 | processor_map_0 << 32 \
                | processor_map_0 << 16 | processor_map_0

        for i, e in enumerate(quantization):
            if e is None:
                quantization[i] = 0  # Only in unused layers

        binary_quantization = any(quantization[ll] == -1 for ll in range(first_layer_used, layers))
        # Check we're not using binary weights on devices that don't support it
        if binary_quantization and not tc.dev.SUPPORT_BINARY_WEIGHTS:
            eprint("Binary weights (-1/+1) are not supported on this device.")

        hw_operator = operator.copy()
        hw_input_dim = copy.deepcopy(input_dim)
        hw_pooled_dim = copy.deepcopy(pooled_dim)
        hw_kernel_size = copy.deepcopy(kernel_size)
        hw_kernel = copy.deepcopy(kernel)
        hw_dilation = copy.deepcopy(dilation)

        # Check that input channels are in separate memory instances if CHW (big) data format is
        # used, and calculate input and output expansion
        for ll in range(first_layer_used, layers):
            if quantization[ll] == 1 and binary_quantization:
                eprint(f"Cannot combine binary quantization in layer {ll} with "
                       "1-bit quantization.")
            if output_shift[ll] is None:
                output_shift[ll] = 0 if not bypass[ll] else 7  # Set default

            if output_shift[ll] < -15 or output_shift[ll] > 15:
                implicit_shift = 8 - abs(quantization[ll]) if not bypass[ll] else 0
                eprint(f"Layer {ll} with {abs(quantization[ll])}-bit weight quantization supports "
                       f"an output_shift range of [{-15 - implicit_shift}, "
                       f"+{15 - implicit_shift}]. The specified value of output_shift is "
                       f"{output_shift[ll] - implicit_shift} which exceeds the system limits.")

            if big_data[ll]:
                p = processor_map[ll] >> (ffs(processor_map[ll]) & ~(tc.dev.P_SHARED-1))
                while p:
                    if popcount(p & (tc.dev.P_SHARED-1)) > 1:
                        eprint(f"Layer {ll} uses CHW input format, but multiple channels "
                               "share the same memory instance. Modify the processor map for "
                               f"layer {ll}.")
                    p >>= tc.dev.P_SHARED

            out_expand[ll] = (output_chan[ll] + tc.dev.MAX_PROC-1) // tc.dev.MAX_PROC
            out_expand_thresh[ll] = (output_chan[ll] + out_expand[ll]-1) // out_expand[ll]
            if output_chan[ll] > tc.dev.MAX_PROC:
                out_expand_thresh[ll] = \
                    min((out_expand_thresh[ll] + tc.dev.P_SHARED-1) & ~(tc.dev.P_SHARED-1),
                        tc.dev.MAX_PROC)
            in_expand[ll] = (input_chan[ll] + tc.dev.MAX_PROC-1) // tc.dev.MAX_PROC
            if tcalc[ll] is None:
                tcalc[ll] = rd_ahead[ll] and in_expand[ll] > 1  # Set default
            in_expand_invol[ll] = (in_expand[ll] + 3) & ~3 if tcalc[ll] else in_expand[ll]
            in_expand_thresh[ll] = (input_chan[ll] + in_expand[ll] - 1) // in_expand[ll]

            if input_chan[ll] > tc.dev.MAX_PROC:
                in_expand_thresh[ll] = \
                    min((in_expand_thresh[ll] + tc.dev.P_SHARED-1) & ~(tc.dev.P_SHARED-1),
                        tc.dev.MAX_PROC)

            assert input_dim[ll][0] * input_dim[ll][1] * in_expand[ll] < tc.dev.FRAME_SIZE_MAX

            # Data memory size check - 4 channels share one instance unless CHW format
            in_size = input_dim[ll][0] * input_dim[ll][1] * in_expand[ll] * operands[ll] \
                * (1 if big_data[ll] else 4)
            if not streaming[ll] and in_size + in_offset[ll] > tc.dev.INSTANCE_WIDTH*16:
                eprint(f'Layer {ll}: {1 if big_data[ll] else 4} channels/word {input_dim[ll][0]}x'
                       f'{input_dim[ll][1]} input (size {in_size}) '
                       f'with input offset 0x{in_offset[ll]:04x} and expansion {in_expand[ll]}x '
                       f'exceeds data memory instance size of {tc.dev.INSTANCE_WIDTH*16}.')

            if operator[ll] != op.CONV1D:
                input_dim_str[ll] = f'{input_dim[ll][0]}x{input_dim[ll][1]}'
                output_dim_str[ll] = f'{output_dim[ll][0]}x{output_dim[ll][1]}'
                kernel_size_str[ll] = f'{kernel_size[ll][0]}x{kernel_size[ll][1]}'
                pool_str[ll] = f'{pool[ll][0]}x{pool[ll][1]}' \
                    if pool[ll][0] > 1 or pool[ll][1] > 1 else '0x0'
                padding_str[ll] = f'{padding[ll][0]}/{padding[ll][1]}'
                pool_stride_str[ll] = f'{pool_stride[ll][0]}/{pool_stride[ll][1]}'
                pool_dilation_str[ll] = f'{pool_dilation[ll][0]}/{pool_dilation[ll][1]}'
                dilation_str[ll] = f'{dilation[ll][0]}/{dilation[ll][1]}'
                stride_str[ll] = f'{stride[ll][0]}/{stride[ll][1]}'
            else:
                input_dim_str[ll] = f'{input_dim[ll][0]}'
                output_dim_str[ll] = f'{output_dim[ll][0]}'
                kernel_size_str[ll] = f'{kernel_size[ll][0]}'
                pool_str[ll] = f'{pool[ll][0]}' \
                    if pool[ll][0] > 1 or pool[ll][1] > 1 else '0'
                padding_str[ll] = f'{padding[ll][0]}'
                pool_stride_str[ll] = f'{pool_stride[ll][0]}'
                pool_dilation_str[ll] = f'{pool_dilation[ll][0]}'
                dilation_str[ll] = f'{dilation[ll][0]}'
                stride_str[ll] = f'{stride[ll][0]}'

                if operands[ll] > 1:
                    eprint('Layer {ll}: Element-wise operations cannot be combined with Conv1d.')

            if dilation[ll][0] > 1:
                if operator[ll] != op.CONV1D:
                    eprint(f'Layer {ll}: `dilation` > 1 is supported for Conv1d only.')

                if kernel_size[ll][0] == 1:
                    eprint(f'Layer {ll}: Kernel length must be greater than 1 to use '
                           '`dilation` > 1.')

                if (kernel_size[ll][0] - 1) * dilation[ll][0] < 9:
                    # Stretch existing kernel if we can
                    # 0 1 2 --> 0 X X X 1 X X X 2
                    kzeros = []
                    for s in range(1, kernel_size[ll][0]):
                        kzeros += [s] * (dilation[ll][0] - 1)
                    k = np.insert(kernel[ll], kzeros, 0, axis=1)
                    hw_kernel[ll] = k
                    hw_kernel_size[ll] = [k.shape[1], 1]
                elif kernel_size[ll][0] <= tc.dev.MAX_DILATION_1D_KERNEL:
                    # Use Conv2d
                    if pool[ll][0] != 1:
                        eprint(f'Layer {ll}: Pooling must be 1 to use `dilation` > 4.')
                    if padding[ll][0] > tc.dev.MAX_DILATION_1D_PAD:
                        eprint(f'Layer {ll}: Padding must be {tc.dev.MAX_DILATION_1D_PAD} '
                               'or smaller to use `dilation` > 4.')
                    if operands[ll] != 1:
                        eprint(f'Layer {ll}: Operands must be 1 to use `dilation` > 4.')
                    if bypass[ll] or flatten[ll] or rd_ahead[ll] or streaming[ll]:
                        eprint(f'Layer {ll}: `bypass`, `flatten`, `rd_ahead`, `streaming` '
                               'must be False to use `dilation` > 4.')
                    if dilation[ll][0] > tc.dev.MAX_DILATION_1D:
                        eprint(f'Layer {ll}: `dilation` must be {tc.dev.MAX_DILATION_1D} '
                               'or smaller for Conv1d operations.')

                    nprint(f'Layer {ll}: Using Conv2d hardware for dilated Conv1d.')
                    # Use the Conv1d hardware with 1 pad on 'dilation' columns using 3x3 kernels
                    hw_operator[ll] = op.CONV2D
                    hw_input_dim[ll][0] = (input_dim[ll][0] + dilation[ll][0] - 1) \
                        // dilation[ll][0]
                    hw_input_dim[ll][1] = dilation[ll][0]
                    hw_pooled_dim[ll] = hw_input_dim[ll]
                    hw_padding[ll] = [1, 1]
                    hw_kernel_size[ll] = [3, 3]
                    hw_dilation[ll] = [1, 1]
                    # 2D output size is equal to the 2D input size since the pad is fixed to 1.
                    # Subtract the original output dimensions to calculate the overage.
                    out_pad[ll] = hw_input_dim[ll][0] * hw_input_dim[ll][1] - output_dim[ll][0]

                    # Create 3x3 kernel from 3x1 kernel -- move original into center column
                    k = np.insert(kernel[ll].reshape(output_chan[ll],
                                                     input_chan[ll] // conv_groups[ll],
                                                     kernel_size[ll][0], -1),
                                  [0, 1], 0, axis=3)
                    if kernel_size[ll][0] == 2:
                        k = np.insert(k, 0, 0, axis=2)  # Insert at top - throw away the padding
                    elif kernel_size[ll][0] == 1:
                        k = np.insert(k, [0, 1], 0, axis=2)  # Use center
                    else:  # 3
                        out_ignore[ll] = 4 * dilation[ll][0] * out_expand[ll]
                    assert k.shape[2] == k.shape[3] == 3
                    hw_kernel[ll] = k.reshape(-1, k.shape[2], k.shape[3])

                    if out_offset[ll] < out_ignore[ll]:
                        eprint(f'Layer {ll}: `out_offset` used with dilation of {dilation[ll][0]} '
                               f'must be at least {out_ignore[ll]:04x}.')
                else:
                    eprint(f'Layer {ll}: Kernel length must be {tc.dev.MAX_DILATION_1D_KERNEL} '
                           f'or smaller to use `dilation` of {dilation[ll][0]}.')

            out_size = (output_dim[ll][0] * output_dim[ll][1] + out_pad[ll]) * out_expand[ll] \
                * 4 * output_width[ll] // 8
            if (not streaming[ll] or ll == terminating_layer) \
               and out_size + out_offset[ll] > tc.dev.INSTANCE_WIDTH*16:
                eprint(f'Layer {ll}: HWC (4 channels/word) '
                       f'{output_width[ll]}-bit {output_dim[ll][0]}x'
                       f'{output_dim[ll][1]} output (size {out_size}) '
                       f'with output offset 0x{out_offset[ll]:04x} and expansion '
                       f'{out_expand[ll]}x '
                       f'exceeds data memory instance size of {tc.dev.INSTANCE_WIDTH*16}.')

            if hw_operator[ll] == op.NONE:
                if activation[ll] is not None:
                    eprint(f'Layer {ll}: Pass-through layers must not use activation.')
                if padding[ll][0] != 0 or padding[ll][1] != 0:
                    eprint(f'Layer {ll}: Padding must be zero for passthrough layers.')
                if output_shift[ll] != 0 and output_shift[ll] is not None:
                    eprint(f'Layer {ll}: `output_shift` must be zero for passthrough layers.')
                if (pool[ll][0] > 1 or pool[ll][1] > 1) \
                   and in_expand[ll] > tc.dev.MAX_POOL_PASSES \
                   and (hw_pooled_dim[ll][0] > 1 or hw_pooled_dim[ll][1] > 1):
                    eprint(f'Layer {ll}: pooling in passthrough layer uses {in_expand[ll]} '
                           f'passes, which exceeds the maximum of {tc.dev.MAX_POOL_PASSES} '
                           'on this device.')

                tram_max[ll] = 1
            else:
                if hw_operator[ll] == op.CONVTRANSPOSE2D:
                    # Flip padding around to match PyTorch conventions for ConvTranspose2d
                    hw_padding[ll] = (
                        hw_dilation[ll][0] * (hw_kernel_size[ll][0] - 1) - hw_padding[ll][0],
                        hw_dilation[ll][1] * (hw_kernel_size[ll][1] - 1) - hw_padding[ll][1]
                    )
                    if hw_padding[ll][0] not in tc.dev.SUPPORTED_X2D_PADS \
                       or hw_padding[ll][1] not in tc.dev.SUPPORTED_X2D_PADS:
                        eprint(f'Layer {ll}: The selected padding ({padding[ll]}) for '
                               'ConvTranspose2d is not supported on this device.')
                    if output_padding[ll][0] not in tc.dev.SUPPORTED_X2D_OUTPUT_PADS \
                       or output_padding[ll][1] not in tc.dev.SUPPORTED_X2D_OUTPUT_PADS:
                        eprint(f'Layer {ll}: The selected output padding ({output_padding[ll]}) '
                               'for ConvTranspose2d is not supported on this device.')
                    tram_max[ll] = max(0, (hw_pooled_dim[ll][1] - 1) * stride[ll][1] + 1
                                       + output_padding[ll][1] + 2 * hw_padding[ll][1]
                                       - hw_kernel_size[ll][1]) + 1
                else:
                    tram_max[ll] = max(0, hw_pooled_dim[ll][1] + 2 * hw_padding[ll][1]
                                       - hw_kernel_size[ll][1]) + 1

            if hw_operator[ll] != op.CONVTRANSPOSE2D and (output_padding[ll][0] != 0
                                                          or output_padding[ll][1] != 0):
                eprint(f'Layer {ll}: Output padding must be 0 for this operator.')

            if input_chan[ll] % conv_groups[ll] != 0 or output_chan[ll] % conv_groups[ll] != 0:
                eprint(f'Layer {ll}: convolution groups ({conv_groups[ll]}) does not divide'
                       f' the input channels ({input_chan[ll]}) or'
                       f' output channels ({output_chan[ll]}).')

            if flatten[ll] and hw_operator[ll] == op.NONE:
                eprint(f'Layer {ll}: `flatten` is not compatible with passthrough layers.')

            if flatten[ll] and (pool[ll][0] > 1 or pool[ll][1] > 1):
                eprint(f'Layer {ll}: `flatten` is not compatible with pooling.')

            if flatten[ll] and streaming[ll]:
                eprint(f'Layer {ll}: `flatten` is not compatible with streaming.')

            if conv_groups[ll] > 1:
                if not tc.dev.SUPPORT_DEPTHWISE:
                    eprint(f'Layer {ll}: convolution groups ({conv_groups[ll]}) > 1 are not '
                           f' supported on this device.')
                if conv_groups[ll] != input_chan[ll] or conv_groups[ll] != output_chan[ll]:
                    eprint(f'Layer {ll}: convolution groups ({conv_groups[ll]}) must be equal to '
                           f'the number of input channels ({input_chan[ll]}), and output '
                           f'channels ({output_chan[ll]}) must be equal to input channels.')
                if flatten[ll]:
                    eprint(f'Layer {ll}: convolution groups ({conv_groups[ll]}) > 1 are not '
                           'supported when flattening.')
                if bias_group_map[ll] is not None:
                    eprint(f'Layer {ll}: `bias_group` is not supported for depth-wise layers.')
                # if output_width[ll] != 8:
                #     eprint(f'Layer {ll}: convolution groups ({conv_groups[ll]}) > 1 are not'
                #            f' supported when using `wide` output.')

            if input_skip[ll] != 0 and not tc.dev.SUPPORT_MULTIPASS_STRIDE:
                eprint(f'Layer {ll}: `in_skip` must be 0 for this device.')

            # Conv1d pool_dilation
            if pool_dilation[ll][0] < 1 or pool_dilation[ll][1] < 1 \
               or pool_dilation[ll][0] > tc.dev.MAX_POOL_DILATION \
               or pool_dilation[ll][1] > tc.dev.MAX_POOL_DILATION:
                eprint(f'Layer {ll}: `pool_dilation` values must be 1 or greater, and '
                       f'{tc.dev.MAX_POOL_DILATION} or smaller on this device.')

            if in_sequences[ll] is not None:
                if operands[ll] == 1:  # cat
                    if write_gap[ll] == 0:
                        min_proc = -1
                        max_proc = -1
                        for _, lt in enumerate(in_sequences[ll]):
                            first_proc = ffs(processor_map[0]) if lt == -1 \
                                else ffs(output_processor_map[lt])
                            last_proc = fls(processor_map[0]) if lt == -1 \
                                else fls(output_processor_map[lt])
                            if first_proc < min_proc:
                                wprint(f'Layer {ll}: In `in_sequences` {in_sequences[ll]}, '
                                       'an earlier layer in the sequence uses a higher first '
                                       f'processor ({min_proc}) than layer {lt} which uses '
                                       f'processor {first_proc}.')
                            if last_proc < max_proc:
                                wprint(f'Layer {ll}: In `in_sequences` {in_sequences[ll]}, '
                                       'an earlier layer in the sequence uses a higher last '
                                       f'processor ({max_proc}) than layer {lt} which uses '
                                       f'processor {last_proc}.')
                            min_proc = first_proc
                            max_proc = last_proc
                else:  # eltwise
                    eltwise_proc_map = 0
                    for _, lt in enumerate(in_sequences[ll]):
                        emap = processor_map[0] if lt == -1 else output_processor_map[lt]
                        if eltwise_proc_map not in (0, emap):
                            eprint(f'Layer {ll}: In `in_sequences` {in_sequences[ll]}, '
                                   'an earlier layer in the sequence uses a different output '
                                   f'processor map (0x{eltwise_proc_map:016x}) than layer {lt} '
                                   f'which uses 0x{emap:016x}.')
                        eltwise_proc_map = emap

                # Merge the output of all processors of all input sequence members
                emap = 0
                for _, lt in enumerate(in_sequences[ll]):
                    emap |= processor_map[0] if lt == -1 else output_processor_map[lt]
                # Check that all out input processors have data from somewhere in the merged map
                if processor_map[ll] & emap != processor_map[ll]:
                    wprint(f'Layer {ll}: The processor map {processor_map[ll]:016x} specifies '
                           'processors that have no data from any of the input sequences '
                           f'{in_sequences[ll]}.')

        # Create comment of the form "k1_b0-1x32x32b_2x2s2p14-..."
        test_name = prefix
        if not embedded_code:
            for ll in range(first_layer_used, layers):
                test_name += f'-{input_chan[ll]}x{input_dim_str[ll]}' \
                             f'{"b" if big_data[ll] else "l"}' \
                             f'{"f" if flatten[ll] else ""}_' \
                             + ("avg" if pool_average[ll]
                                and (pool[ll][0] > 1 or pool[ll][1] > 1) else "") \
                             + ("max" if not pool_average[ll]
                                and (pool[ll][0] > 1 or pool[ll][1] > 1) else "") \
                             + f'{pool_str[ll]}s{pool_stride[ll][0]}' \
                             f'p{padding[ll][0]}' \
                             f'm{output_chan[ll]}'
                if activation[ll] == op.ACT_RELU:
                    test_name += "_relu"
                elif activation[ll] == op.ACT_ABS:
                    test_name += "_abs"
            if repeat_layers > 1:
                test_name += f'_repeat{repeat_layers}'
        MAX_PATH = 255
        if len(test_name) + len(base_directory) > MAX_PATH - 10:
            h = hashlib.md5(test_name.encode()).hexdigest()  # Immutable hash from test name
            cutoff = MAX_PATH - len(test_name) - len(base_directory) - len(h) - 10
            test_name = test_name[:cutoff] + '-' + h
        print(f'{test_name}...')

        try:
            target_dir = os.path.join(base_directory, test_name)
            os.makedirs(target_dir, exist_ok=False)
        except OSError:
            if not overwrite:
                eprint('The target folder', target_dir, 'exists. Use --overwrite to proceed.')
            else:
                wprint('--overwrite specified, writing to', target_dir, 'even though it exists.')

        # Redirect stdout?
        if log:
            sys.stdout = open(os.path.join(base_directory, test_name, log_filename), 'w')
            print(f'{" ".join(str(x) for x in sys.argv)}')
            print(f'{tc.dev.partnum}\n')
            print(f'{test_name}')

        if block_mode:
            filename = state.input_filename + '.mem'
        else:
            filename = c_filename + ('_riscv' if riscv else '') + '.c'
        if not block_mode and (embedded_code or compact_data):
            sampledata_header = \
                open(os.path.join(base_directory, test_name, state.sample_filename), mode='w')
            if state.generate_kat and state.result_filename is not None:
                sampleoutput_header = \
                    open(os.path.join(base_directory, test_name, state.result_filename), mode='w')
            else:
                sampleoutput_header = None
        else:
            sampledata_header = sampleoutput_header = None
        if not block_mode and (embedded_code or compact_weights):
            weight_header = \
                open(os.path.join(base_directory, test_name, weight_filename), mode='w')
        else:
            weight_header = None

        # Calculate the groups needed, and groups and processors used overall
        processors_used = 0
        group_map = [None] * layers
        broadcast_mode = [None] * layers
        emulate_eltwise = [False] * layers
        for ll in range(first_layer_used, layers):
            bits = processor_map[ll]
            processors_used |= bits

            if input_chan[ll] > tc.dev.MAX_CHANNELS:
                eprint(f'Layer {ll} is configured for {input_chan[ll]} input channels, which '
                       f'exceeds the system maximum of {tc.dev.MAX_CHANNELS}.')
            if output_chan[ll] > tc.dev.MAX_CHANNELS:
                eprint(f'Layer {ll} is configured for {output_chan[ll]} output channels, which '
                       f'exceeds the system maximum of {tc.dev.MAX_CHANNELS}.')
            if (ll != start_layer or not fast_fifo_quad) \
               and popcount(processor_map[ll]) != in_expand_thresh[ll]:
                eprint(f'Layer {ll} has {input_chan[ll]} input channels using {in_expand[ll]} '
                       f'passes, and {operands[ll]} operands ({in_expand_thresh[ll]} processors '
                       f'per pass), but the enabled processor map 0x{processor_map[ll]:016x} '
                       f'has {popcount(processor_map[ll])} bits instead of the '
                       f'expected number of {in_expand_thresh[ll]}.')
            if ll == start_layer and fast_fifo_quad \
               and popcount(processor_map_0) != in_expand_thresh[ll]:
                eprint(f'Layer {ll} has {input_chan[ll]} input channels using {in_expand[ll]} '
                       f'passes ({in_expand_thresh[ll]} processors per pass), but the '
                       f'enabled processor map 0x{processor_map[ll]:016x} '
                       f'has {popcount(processor_map[ll])} bits instead of the '
                       f'expected number of {in_expand_thresh[ll]}.')
            if popcount(output_processor_map[ll]) != out_expand_thresh[ll]:
                eprint(f'Layer {ll} has {output_chan[ll]} output channels using {out_expand[ll]} '
                       f'passes ({out_expand_thresh[ll]} processors per pass), but the '
                       f'processor output map 0x{output_processor_map[ll]:016x} '
                       f'has {popcount(output_processor_map[ll])} bits instead of the '
                       f'expected number of {out_expand_thresh[ll]}.')
            this_map = []
            for group in range(tc.dev.P_NUMGROUPS):
                if (processor_map[ll] >> group*tc.dev.P_NUMPRO) % 2**tc.dev.P_NUMPRO:
                    this_map.append(group)
            group_map[ll] = this_map

            # Ensure input and output map are the same for passthrough layers
            if hw_operator[ll] == op.NONE:
                for group in range(tc.dev.P_NUMGROUPS):
                    in_pro = 2**popcount(
                        (processor_map[ll] >> group*tc.dev.P_NUMPRO) % 2**tc.dev.P_NUMPRO
                    ) - 1
                    out_pro = (output_processor_map[ll] >> group*tc.dev.P_NUMPRO) \
                        % 2**tc.dev.P_NUMPRO
                    if out_pro != 0:
                        out_pro >>= ffs(out_pro)
                    if out_pro != in_pro:
                        eprint(f'Layer {ll} is a pass-through layer. The output processors must '
                               'be a packed version of the input processors for each x16. '
                               f'Configured are: input {processor_map[ll]:016x}, output '
                               f'{output_processor_map[ll]:016x}.')

            # Ensure byte positions are the same in the input and output map for
            # depthwise convolutions
            if conv_groups[ll] > 1:
                if ffs(output_processor_map[ll]) % tc.dev.P_SHARED != 0:
                    eprint(f'Layer {ll} is a depth-wise convolution. Output processors '
                           'must be aligned to a multiple of 4. Configured for this layer: '
                           f'{output_processor_map[ll]:016x}.')
                if ffs(processor_map[ll]) % tc.dev.P_SHARED != 0 \
                   and (processor_map[ll] >> ffs(processor_map[ll])) // 2**tc.dev.P_NUMPRO > 0:
                    eprint(f'Layer {ll} is a depth-wise convolution. When spanning groups, '
                           'processors must be aligned to a multiple of 4. Configured for this '
                           f'layer: {processor_map[ll]:016x}.')
                if processor_map[ll] != output_processor_map[ll]:
                    wprint(f'Layer {ll}: depth-wise convolution moves data across processors. '
                           f'This has a performance impact. Input {processor_map[ll]:016x}, '
                           f'output {output_processor_map[ll]:016x}.')
                    broadcast_mode[ll] = False
                else:
                    broadcast_mode[ll] = True

            # Block certain element-wise operations when not using passthrough mode
            if tc.dev.EMULATE_ELTWISE_MP and operands[ll] > 1 and in_expand[ll] > 1 \
               and operands[ll] * in_expand[ll] != operands[ll] + in_expand[ll]:
                if hw_operator[ll] != op.NONE or pool[ll][0] > 1 or pool[ll][1] > 1 \
                   or pool_stride[ll][0] > 1 or pool_stride[ll][1] > 1:
                    eprint(f'The element-wise operation in layer {ll} exceeds a multi-pass of 2 '
                           'and therefore does not support pooling or convolution.')
                emulate_eltwise[ll] = True

            # Warn if hidden layers use channel count that is not divisible by 4
            if ll != start_layer and input_chan[ll] % 4 != 0:
                nprint(f'Layer {ll} uses an input channel count ({input_chan[ll]}) that is not '
                       'a multiple of 4. Best energy performance is achieved with multiples of 4.')

        groups_used = []
        for group in range(tc.dev.P_NUMGROUPS):
            if ((processors_used |
                 output_processor_map[final_layer]) >> group*tc.dev.P_NUMPRO) % 2**tc.dev.P_NUMPRO:
                groups_used.append(group)

        if 0 not in groups_used:
            eprint('Group 0 is not used, this is currently unsupported.')

        for ll in range(first_layer_used, layers):
            if bias_group_map[ll] is not None:
                for _, e in enumerate(bias_group_map[ll]):
                    if e not in groups_used:
                        eprint(f'Layer {ll}: `bias_group` references the unused group {e}. '
                               f'Used x16 groups for this network are: {groups_used}.',
                               error=not ignore_bias_groups)

        # Create ARM code wrapper if needed
        if riscv and not block_mode:
            with open(os.path.join(base_directory, test_name, c_filename + '.c'), mode='w') as f:
                apb = apbaccess.apbwriter(
                    f,
                    master=False,
                    riscv=False,
                    embedded_arm=embedded_code,
                    groups=list(set().union(groups_used)),
                    test_name=test_name,
                )
                apb.copyright_header()

                apb.output(f'// ARM wrapper code\n// {test_name}\n')
                apb.output(f'// Created using {" ".join(str(x) for x in sys.argv)}\n\n')

                apb.header()
                apb.main()

        if input_csv is not None:
            csv = os.path.join(base_directory, test_name, input_csv)
        else:
            csv = None

        if embedded_code and api_filename.lower() != 'none':
            apifile = open(os.path.join(base_directory, test_name, api_filename), mode='w')
        else:
            apifile = None

        with open(os.path.join(base_directory, test_name, filename), mode='w') as memfile:
            apb = apbaccess.apbwriter(
                memfile,
                verify_writes=verify_writes,
                weight_header=weight_header,
                sampledata_header=sampledata_header,
                sampleoutput_header=sampleoutput_header,
                embedded_code=embedded_code,
                write_zero_registers=write_zero_regs,
                master=groups_used[0]
                if oneshot > 0 or stopstart or (apifile is not None) else False,
                riscv=True if riscv else None,
                fast_fifo=fast_fifo,
                input_chan=input_chan[start_layer],
                apifile=apifile,
                forever=forever,
                fifo=fifo,
                groups=list(set().union(groups_used)),
                oneshot=terminating_layer if oneshot else 0,
                num_classes=output_chan[terminating_layer],
                output_width=output_width[terminating_layer],
                bias=any(b is not None for b in bias),
                test_name=test_name,
            )

            apb.copyright_header()

            apb.output(f'// {test_name}\n')
            apb.output(f'// Created using {" ".join(str(x) for x in sys.argv)}\n\n')
            if apifile is not None:
                apb.output(f'// {test_name}\n', True)
                apb.output(f'// Created using {" ".join(str(x) for x in sys.argv)}\n\n', True)
                apb.output('// DO NOT EDIT - regenerate this file instead!\n\n', True)

            # Human readable description of test
            apb.output(f'// Configuring {repeat_layers * layers} '
                       f'layer{"s" if repeat_layers * layers > 1 else ""}:\n', embedded_code)

            for r in range(repeat_layers):
                for ll in range(first_layer_used, layers):
                    flatten_str = "" if not flatten[ll] else \
                        f"flattened to {input_chan[ll]*input_dim[ll][0]*input_dim[ll][1]}x1x1, "
                    apb.output(f'// Layer {r * layers + ll}: '
                               f'{str(operands[ll])+"x" if operands[ll] > 1 else ""}'
                               f'{input_chan[ll]}x{input_dim_str[ll]} ('
                               f'{"streaming " if streaming[ll] else ""}{flatten_str}'
                               f'{"CHW data)" if big_data[ll] else "HWC data)"}, ',
                               embedded_code)
                    if pool[ll][0] > 1 or pool[ll][1] > 1:
                        apb.output(f'{pool_str[ll]} {"avg" if pool_average[ll] else "max"} '
                                   f'pool with stride {pool_stride_str[ll]}', embedded_code)
                        if pool_dilation[ll][0] > 1 or pool_dilation[ll][1] > 1:
                            apb.output(f' and dilation {pool_dilation_str[ll]}', embedded_code)
                    else:
                        apb.output('no pooling', embedded_code)
                    if hw_operator[ll] != op.NONE:
                        conv_str = f', {op.string(operator[ll])} with kernel size ' \
                                   f'{kernel_size_str[ll]}, ' \
                                   f'stride {stride_str[ll]}, ' \
                                   f'pad {padding_str[ll]}, ' \
                                   f'{op.act_string(activation[ll])}, '
                        if dilation[ll][0] > 1 or dilation[ll][1] > 1:
                            conv_str += f'dilation {dilation_str[ll]}, '
                    else:
                        conv_str = ', no convolution, '
                    apb.output(conv_str +
                               f'{output_chan[ll]}x{output_dim_str[ll]} output\n', embedded_code)

            apb.output('\n', embedded_code)

            apb.header()

            if embedded_code or compact_data or mexpress:
                apb.function_header(prefix='', function='memcpy32', return_type='void',
                                    arguments='uint32_t *dst, const uint32_t *src, int n')
                apb.output('  while (n-- > 0) {\n'
                           '    *dst++ = *src++;\n'
                           '  }\n', embedded_code)
                apb.function_footer(return_value='void')  # memcpy32()

            if state.input_fifo:
                apb.output('#define USE_FIFO\n')

            if embedded_code or compact_data or input_csv:
                # Pre-define data memory loader. Inline later when generating RTL sim.
                load.load(
                    True,
                    apb,
                    big_data[start_layer],
                    processor_map_0,
                    in_offset[start_layer],
                    [input_chan[start_layer], input_dim[start_layer][0],
                     input_dim[start_layer][1]],
                    in_expand[start_layer],
                    operands[start_layer],
                    in_expand_thresh[start_layer],
                    data,
                    hw_padding[start_layer],
                    csv_file=csv,
                )
            if not block_mode and (embedded_code or compact_weights):
                # Pre-define the kernels and bias values
                kern_offs, kern_len, kern_count, kern_ochan = kernels.load(
                    True,
                    apb,
                    layers,
                    hw_operator,
                    hw_kernel,
                    hw_kernel_size,
                    quantization,
                    processor_map,
                    output_processor_map,
                    input_chan,
                    output_chan,
                    out_expand,
                    out_expand_thresh,
                    in_expand,
                    in_expand_thresh,
                    conv_groups,
                    flatten,
                    verify_kernels,
                    api=embedded_code,
                )
                bias_offs, bias_group, group_bias_max = kbias.load(
                    True,
                    apb,
                    layers,
                    bias,
                    group_map,
                    bias_group_map,
                    output_chan,
                    streaming,
                    conv_groups,
                    broadcast_mode,
                    processor_map,
                    output_processor_map,
                    out_expand,
                    list(set().union(groups_used)),
                    flatten,
                )

            apb.function_header(function='init')

            # Initialize CNN registers

            if verbose:
                # startup, lat = stats.calc_latency(
                #     streaming,
                #     layers,
                #     eltwise,
                #     pool,
                #     pooled_dim,
                #     in_expand,
                #     output_chan,
                #     output_dim,
                #     input_dim,
                #     padding,
                #     kernel_size,
                # )
                # print('\nEstimated latency:')
                # print('------------------')
                # if lat is None:
                #     print('N/A')
                # else:
                #     total = startup
                #     print(f'Startup{startup:14,}')
                #     for k in range(first_layer_used, layers):
                #         total += lat[k][0]
                #         print(f'Layer {k:<3}{lat[k][0]:12,}', end='')
                #         if debug_latency:
                #             print('', lat[k][1])
                #         else:
                #             print('')
                #     print('           ==========')
                #     print(f'Total{total:16,} cycles')

                print('\nGlobal registers:')
                print('-----------------')

            if tc.dev.REQUIRE_REG_CLEAR:
                for _, group in enumerate(groups_used):
                    apb.write_ctl(group, tc.dev.REG_CTL, 1 << 3 | tc.dev.READY_SEL << 1,
                                  comment=' // Enable clocks', no_verify=True)
            # Reset
            apb.write_fifo_ctl(tc.dev.AON_CTL, tc.dev.AON_READY_SEL,
                               comment=' // AON control', force_write=True)

            if tc.dev.REQUIRE_REG_CLEAR:
                bist_clear = tc.dev.BIST_ZERO_BOTH_EX if any(b is not None for b in bias) \
                    else tc.dev.BIST_ZERO_EX
                for _, group in enumerate(groups_used):
                    apb.write_ctl(group, tc.dev.REG_SRAM_TEST, bist_clear,
                                  comment=' // Clear registers', no_verify=True)
                for _, group in enumerate(groups_used):
                    apb.wait_ctl(group, tc.dev.REG_SRAM_TEST,
                                 tc.dev.BIST_ZERO_WAIT, tc.dev.BIST_ZERO_WAIT,
                                 comment=' // Wait for clear')
                for _, group in enumerate(groups_used):
                    apb.write_ctl(group, tc.dev.REG_SRAM_TEST, 0,
                                  comment=' // Reset BIST', force_write=True, no_verify=True)
                apb.output('\n', embedded_code)

            # Configure global control registers for used groups
            for _, group in enumerate(groups_used):
                if init_tram:
                    # Zero out Tornado RAM
                    if not embedded_code:
                        for p in range(tc.dev.P_NUMPRO):
                            for offs in range(tc.dev.TRAM_SIZE):
                                apb.write_tram(group, p, offs, 0, comment='Zero ')
                        apb.output('\n', embedded_code)
                    else:
                        for p in range(tc.dev.P_NUMPRO):
                            addr = apb_base + tc.dev.C_GROUP_OFFS*group + tc.dev.C_TRAM_BASE \
                                + p * tc.dev.TRAM_OFFS * 4
                            apb.output(f'  memset((uint32_t *) 0x{addr:08x}, 0, '
                                       f'{tc.dev.TRAM_SIZE}); // Zero TRAM {group}\n',
                                       embedded_code)
                            apb.output('\n', embedded_code)

                # Stop state machine - will be overwritten later; enable FIFO
                val = tc.dev.READY_SEL << 1
                if fifo:
                    val |= 1 << 15
                val |= 1 << 3  # Enable clocks
                if mexpress:
                    val |= 1 << 20
                apb.write_ctl(group, tc.dev.REG_CTL, val,
                              comment=' // Stop SM')
                # SRAM Control - does not need to be changed
                apb.write_ctl(group, tc.dev.REG_SRAM, 0x40e,
                              comment=' // SRAM control')
                # Number of layers and start layer
                val = (repeat_layers * final_layer) | (start_layer << 8)
                apb.write_ctl(group, tc.dev.REG_LCNT_MAX, val,
                              comment=' // Layer count')

            if zero_sram:
                for group in range(tc.dev.P_NUMGROUPS):
                    apb.write_ctl(group, tc.dev.REG_SRAM_TEST, tc.dev.BIST_DATA_EX,
                                  comment=' // Data SRAM BIST')
                for group in range(tc.dev.P_NUMGROUPS):
                    apb.wait_ctl(group, tc.dev.REG_SRAM_TEST,
                                 tc.dev.BIST_DATA_WAIT, tc.dev.BIST_DATA_WAIT,
                                 comment=' // Wait for BIST')
                for group in range(tc.dev.P_NUMGROUPS):
                    apb.verify_ctl(group, tc.dev.REG_SRAM_TEST, tc.dev.BIST_DATA_ERR, 0,
                                   comment=' // Return on BIST error')
                for group in range(tc.dev.P_NUMGROUPS):
                    apb.write_ctl(group, tc.dev.REG_SRAM_TEST, 0,
                                  comment=' // Reset BIST', force_write=True)
                apb.output('\n', embedded_code)
                for group in range(tc.dev.P_NUMGROUPS):
                    apb.write_ctl(group, tc.dev.REG_SRAM_TEST, tc.dev.BIST_MASK_EX,
                                  comment=' // Mask SRAM BIST')
                for group in range(tc.dev.P_NUMGROUPS):
                    apb.wait_ctl(group, tc.dev.REG_SRAM_TEST,
                                 tc.dev.BIST_MASK_WAIT, tc.dev.BIST_MASK_WAIT,
                                 comment=' // Wait for BIST')
                for group in range(tc.dev.P_NUMGROUPS):
                    apb.verify_ctl(group, tc.dev.REG_SRAM_TEST, tc.dev.BIST_MASK_ERR, 0,
                                   comment=' // Return on BIST error')
                for group in range(tc.dev.P_NUMGROUPS):
                    apb.write_ctl(group, tc.dev.REG_SRAM_TEST, 0,
                                  comment=' // Reset BIST', force_write=True)
                apb.output('\n', embedded_code)
                for group in range(tc.dev.P_NUMGROUPS):
                    apb.write_ctl(group, tc.dev.REG_SRAM_TEST, tc.dev.BIST_TRAM_EX,
                                  comment=' // Tornado SRAM BIST')
                for group in range(tc.dev.P_NUMGROUPS):
                    apb.wait_ctl(group, tc.dev.REG_SRAM_TEST,
                                 tc.dev.BIST_TRAM_WAIT, tc.dev.BIST_TRAM_WAIT,
                                 comment=' // Wait for BIST')
                for group in range(tc.dev.P_NUMGROUPS):
                    apb.verify_ctl(group, tc.dev.REG_SRAM_TEST, tc.dev.BIST_TRAM_ERR, 0,
                                   comment=' // Return on BIST error')
                for group in range(tc.dev.P_NUMGROUPS):
                    apb.write_ctl(group, tc.dev.REG_SRAM_TEST, 0,
                                  comment=' // Reset BIST', force_write=True)
                apb.output('\n', embedded_code)
                for group in range(tc.dev.P_NUMGROUPS):
                    apb.write_ctl(group, tc.dev.REG_SRAM_TEST, tc.dev.BIST_BIAS_EX,
                                  comment=' // Bias Rfile BIST')
                for group in range(tc.dev.P_NUMGROUPS):
                    apb.wait_ctl(group, tc.dev.REG_SRAM_TEST,
                                 tc.dev.BIST_BIAS_WAIT, tc.dev.BIST_BIAS_WAIT,
                                 comment=' // Wait for BIST')
                for group in range(tc.dev.P_NUMGROUPS):
                    apb.verify_ctl(group, tc.dev.REG_SRAM_TEST, tc.dev.BIST_BIAS_ERR, 0,
                                   comment=' // Return on BIST error')
                for group in range(tc.dev.P_NUMGROUPS):
                    apb.write_ctl(group, tc.dev.REG_SRAM_TEST, 0,
                                  comment=' // Reset BIST', force_write=True)
                apb.output('\n', embedded_code)

            apb.function_footer()

            if block_mode or not (embedded_code or compact_weights):
                kern_offs, kern_len, kern_count, kern_ochan = kernels.load(
                    embedded_code,
                    apb,
                    layers,
                    hw_operator,
                    hw_kernel,
                    hw_kernel_size,
                    quantization,
                    processor_map,
                    output_processor_map,
                    input_chan,
                    output_chan,
                    out_expand,
                    out_expand_thresh,
                    in_expand,
                    in_expand_thresh,
                    conv_groups,
                    flatten,
                    verify_kernels,
                )
                bias_offs, bias_group, group_bias_max = kbias.load(
                    embedded_code,
                    apb,
                    layers,
                    bias,
                    group_map,
                    bias_group_map,
                    output_chan,
                    streaming,
                    conv_groups,
                    broadcast_mode,
                    processor_map,
                    output_processor_map,
                    out_expand,
                    list(set().union(groups_used)),
                    flatten,
                )

            if verbose:
                print('\nGlobal configuration:')
                print('---------------------')
                print(f'Used processors     = 0x{processors_used:016x}')
                print(f'Used groups         = {groups_used}')
                if start_layer > 0:
                    print(f'Starting layer      = {start_layer}')
                if any(s != i+1 and (s != -1 or i != final_layer)
                       for i, s in enumerate(next_sequence)):
                    print('Next layer sequence = [',
                          ', '.join(str(k) if k != -1 else 'stop' for k in next_sequence), ']',
                          sep='',)

                print('\nPer-group configuration:')
                print('-----------------------')
                print(f'Used bias memory    = {group_bias_max}')

                print('\nPer-layer configuration:')
                print('------------------------')
                if repeat_layers > 1:
                    print(f'Layer repeat count  = {repeat_layers}')
                print(f'Group map           = {group_map}')

                print('Input offset        = [',
                      ', '.join('0x{:04x}'.format(k) if k is not None
                                else 'N/A' for k in in_offset), ']', sep='',)
                print(f'Streaming           = {streaming}')
                print(f'Input channels      = {input_chan}')
                print(f'Input dimensions    = {input_dim}')
                print(f'Flatten             = {flatten}')
                if any(s > 0 for s in input_skip):
                    print(f'Input skip          = {input_skip}')
                if any(s > 0 for s in input_channel_skip):
                    print(f'Input channel skip  = {input_channel_skip}')
                print(f'Input expansion     = {in_expand}')
                print(f'Expansion threshold = {in_expand_thresh}')

                print(f'Pooling             = {pool}')
                if any(h != 1 or w != 1 for h, w in pool_dilation):
                    print(f'Pooling dilation    = {pool_dilation}')
                print(f'Pooling stride      = {pool_stride}')
                print(f'Pooled dimensions   = {pooled_dim}')

                print('Processor map       = [',
                      ', '.join('0x{:016x}'.format(k) for k in processor_map), ']', sep='',)

                print('Element-wise op     = [',
                      ', '.join(op.string(k, elt=True) for k in eltwise), ']', sep='',)
                print(f'Operand expansion   = {operands}')

                print(f'Output channels     = {output_chan}')
                print(f'Output dimensions   = {output_dim}')
                print(f'Output expansion    = {out_expand}')
                print(f'Expansion threshold = {out_expand_thresh}')
                print(f'Output shift        = {output_shift}')
                print('Output processors   = [',
                      ', '.join('0x{:016x}'.format(k) if k is not None
                                else 'N/A' for k in output_processor_map), ']', sep='',)
                print(f'Output data bits    = {output_width}')

                print(f'Group with bias     = {bias_group}')
                print(f'Bias offset         = {bias_offs}')

                print('Output offset       = [',
                      ', '.join('0x{:04x}'.format(k) for k in out_offset), ']', sep='',)

                print('Operator            = [',
                      ', '.join(op.string(k) for k in operator), ']', sep='',)
                print('Activation          = [',
                      ', '.join(op.act_string(k) if k is not None
                                else 'no' for k in activation), ']', sep='',)
                print(f'Kernel offset       = {kern_offs}')
                print(f'Kernel length       = {kern_len}')
                print(f'Kernel count        = {kern_count}')
                print(f'Kernel dimensions   = {kernel_size}')
                if any(h != 1 or w != 1 for h, w in dilation):
                    print(f'Dilation            = {dilation}')
                print('Kernel size (bits)  = [',
                      ', '.join(str(k) if k >= 0
                                else 'b' for k in quantization), ']', sep='',)
                if any(bypass):
                    print(f'Kernel bypass       = {bypass}')
                print(f'Convolution groups  = {conv_groups}')
                print(f'Padding             = {padding}')
                print(f'Stride              = {stride}')
                print('')

            if verbose:
                print('Layer register configuration:')
                print('-----------------------------')

            apb.function_header(function='configure')

            # Configure per-layer control registers
            for r in range(repeat_layers):
                for ll in range(first_layer_used, layers):

                    local_source = False
                    for _, group in enumerate(groups_used):
                        # Local output must be used:
                        # - for depthwise convolutions
                        # - When parallel processing is enabled (not currently supported), or
                        # - When there are gaps in the output, and
                        #   - the gaps are non-uniform, or
                        #   - the layer is in passthrough mode
                        # Uniform gaps (when not in passthrough mode) can be achieved using the
                        # time slot offset.

                        if local_source:
                            break

                        gap_max, gap_min = 0, tc.dev.MAX_PROC
                        gmap = \
                            output_processor_map[ll] & 2**tc.dev.P_NUMPRO - 1 << \
                            group*tc.dev.P_NUMPRO
                        if popcount(gmap) > 1:
                            p = ffs(gmap)
                            while p < fls(gmap):
                                gap = ffs(gmap & ~(2**(p+1) - 1)) - p - 1
                                gap_min, gap_max = min(gap, gap_min), max(gap, gap_max)
                                p += gap + 1
                            local_source = \
                                gap_min != gap_max or gap_max > 0 and hw_operator[ll] == op.NONE

                        # FIXME: Check that we don't overlap by-16 groups when in local_source mode
                        # FIXME: Non-uniform gaps are not supported

                    # For passthrough, determine time slot count (maximum across all used groups)
                    tscnt_max = 0
                    for _, group in enumerate(groups_used):
                        if hw_operator[ll] == op.NONE:
                            if popcount((processor_map[ll] >> group*tc.dev.P_NUMPRO)
                                        % 2**tc.dev.P_NUMPRO) != 0:
                                tscnt_max = max(
                                    tscnt_max,
                                    (popcount((processor_map[ll] >> group*tc.dev.P_NUMPRO)
                                              % 2**tc.dev.P_NUMPRO)
                                     * output_width[ll] // 8 - 1) // 4
                                )
                        elif conv_groups[ll] > 1:
                            if broadcast_mode[ll]:
                                pop = popcount((processor_map[ll] >> group*tc.dev.P_NUMPRO)
                                               % 2**tc.dev.P_NUMPRO)
                                tscnt_max = max(
                                    tscnt_max,
                                    (min(pop - 1, 3) + 1) * (output_width[ll] // 8) - 1
                                )
                            else:
                                tscnt_max = max(
                                    tscnt_max,
                                    (popcount((processor_map[ll] >> group*tc.dev.P_NUMPRO)
                                              % 2**tc.dev.P_NUMPRO)
                                     * output_width[ll] + 7) // 8 - 1
                                )

                    for _, group in enumerate(groups_used):
                        apb.output(f'  // Layer {r * layers + ll} group {group}\n', embedded_code)

                        val = 0
                        if link_layer:
                            if ll != final_layer:
                                val = 1 << 7 | (ll + 1)
                            else:
                                val = 1 << 8  # Stop
                        else:
                            lt = next_sequence[ll]
                            if lt == -1:
                                if ll != layers - 1:  # Don't set stop bit unless required
                                    val = 1 << 8
                            elif lt != ll + 1:
                                val = 1 << 7 | lt
                            elif snoop_sequence[ll] is not None:
                                lt = snoop_sequence[ll]
                                assert lt >= 0
                                val = 1 << 7 | lt
                            if lt != -1:
                                if in_sequences[lt] is not None and ll in in_sequences[lt] \
                                   and operands[lt] == 1:
                                    ll_index = in_sequences[lt].index(ll)
                                    ll_offset = out_offset[ll] - ll_index * write_gap[ll] * 4
                                    if in_offset[lt] != ll_offset:
                                        wprint(f'Layer {ll}: The input offset of the next '
                                               f'sequence (layer {lt}, 0x{in_offset[lt]:04x}) '
                                               "does not match the current layer's output "
                                               f'(offset 0x{out_offset[ll]:04x} - write gap '
                                               f'{write_gap[ll]} * sequence position '
                                               f'{ll_index} * 4 = 0x{ll_offset:04x}).')
                                    if input_chan[lt] != output_chan[ll] \
                                       * len(in_sequences[lt]) \
                                       or input_dim[lt] != output_dim[ll]:
                                        wprint(f'Layer {ll}: The input dimensions of the next '
                                               f'sequence (layer {lt}, '
                                               f'{len(in_sequences[lt])} inputs, '
                                               f'{input_chan[lt]}x{input_dim_str[lt]}) do '
                                               "not match the current layer's output "
                                               "dimensions "
                                               f'({output_chan[ll]}x{output_dim_str[ll]}).')

                        if hasattr(tc.dev, 'LREG_NXTLYR'):
                            apb.write_lreg(group, r * layers + ll, tc.dev.LREG_NXTLYR, val,
                                           comment=' // Next Layer')

                        # Configure row count
                        if flatten[ll]:
                            in_row = pool[ll][0]
                            in_col = pool[ll][1]
                        else:
                            if hw_operator[ll] == op.CONVTRANSPOSE2D:
                                in_row = stride[ll][0] * hw_input_dim[ll][0]
                                in_col = stride[ll][1] * hw_input_dim[ll][1]
                            elif hw_operator[ll] == op.NONE and emulate_eltwise[ll]:
                                in_row = hw_input_dim[ll][0] * in_expand[ll]
                                in_col = hw_input_dim[ll][1]
                            else:
                                in_row = hw_input_dim[ll][0]
                                in_col = hw_input_dim[ll][1]
                        if hasattr(tc.dev, 'CNT_DIFF_OFFS'):
                            diff = (in_row - ((in_row - pool[ll][0] - pool_dilation[ll][0] + 1)
                                              // pool_stride[ll][0]) * pool_stride[ll][0])
                            val = in_row - diff  # Stop row, 0-based
                            assert val < 2**tc.dev.MAX_CNT_BITS

                            # Stop column
                            if hw_operator[ll] == op.CONV1D:
                                diff = 1
                            else:
                                diff = (in_col - ((in_col - pool[ll][1] - pool_dilation[ll][1] + 1)
                                                  // pool_stride[ll][1]) * pool_stride[ll][1])
                            # Bytes to next starting element
                            diff = (diff + (pool_stride[ll][0] - 1) * in_col) \
                                * (input_skip[ll] + 1) * operands[ll] * in_expand[ll]

                            val |= diff << tc.dev.CNT_DIFF_OFFS
                            if hw_padding[ll][0] > 0:
                                assert hw_padding[ll][0] - 1 < 2**2
                                val |= 1 << tc.dev.PAD_ENA_OFFS
                                val |= hw_padding[ll][0] - 1 << tc.dev.PAD_CNT_OFFS
                        else:
                            val = in_row - 1
                            assert hw_padding[ll][0] < 2**2
                            assert val + 2*hw_padding[ll][0] < 2**tc.dev.MAX_CNT_BITS
                            val |= hw_padding[ll][0] << tc.dev.PAD_CNT_OFFS
                            val += 2*hw_padding[ll][0]
                        apb.write_lreg(group, r * layers + ll, tc.dev.LREG_RCNT, val,
                                       comment=' // Rows')

                        # Configure column count (evaluates to 0 for 1D convolutions)
                        if hasattr(tc.dev, 'CNT_DIFF_OFFS'):
                            # Calculate last pooling fetch before advancing to next row
                            diff = (in_col - ((in_col - pool[ll][1] - pool_dilation[ll][1] + 1)
                                              // pool_stride[ll][1]) * pool_stride[ll][1])
                            val = in_col - diff
                            assert val < 2**tc.dev.MAX_CNT_BITS
                            val |= diff << tc.dev.CNT_DIFF_OFFS
                            if hw_padding[ll][1] > 0:
                                assert hw_padding[ll][1] - 1 < 2**2
                                val |= 1 << tc.dev.PAD_ENA_OFFS
                                val |= hw_padding[ll][1] - 1 << tc.dev.PAD_CNT_OFFS
                        else:
                            val = in_col - 1
                            assert hw_padding[ll][1] < 2**2
                            assert val + 2 * hw_padding[ll][1] < 2**tc.dev.MAX_CNT_BITS
                            val |= hw_padding[ll][1] << tc.dev.PAD_CNT_OFFS
                            val += 2 * hw_padding[ll][1]
                        apb.write_lreg(group, r * layers + ll, tc.dev.LREG_CCNT, val,
                                       comment=' // Columns')

                        # Configure pooling row count
                        val = (pool[ll][0] - 1) * pool_dilation[ll][0]
                        assert val < 2**4
                        if hasattr(tc.dev, 'CNT_INC_OFFS'):
                            val |= pool_dilation[ll][0] - 1 << tc.dev.CNT_INC_OFFS
                        apb.write_lreg(group, r * layers + ll, tc.dev.LREG_PRCNT, val,
                                       comment=' // Pooling rows')

                        # Configure pooling column count
                        val = (pool[ll][1] - 1) * pool_dilation[ll][1]
                        assert val < 2**4
                        if hasattr(tc.dev, 'CNT_INC_OFFS'):
                            val |= pool_dilation[ll][1] - 1 << tc.dev.CNT_INC_OFFS
                        apb.write_lreg(group, r * layers + ll, tc.dev.LREG_PCCNT, val,
                                       comment=' // Pooling columns')

                        # Configure pooling stride count
                        if hw_operator[ll] == op.CONVTRANSPOSE2D:
                            val = 0
                        elif pool_stride[ll][0] > 1:
                            val = pool_stride[ll][0]-1
                        else:
                            val = stride[ll][0]-1
                        assert val < 2**4
                        if hasattr(tc.dev, 'MP_STRIDE_OFFS'):  # Multipass stride
                            val |= pool_stride[ll][0] * operands[ll] * in_expand[ll] \
                                * (input_skip[ll] + 1) << tc.dev.MP_STRIDE_OFFS
                        apb.write_lreg(group, r * layers + ll, tc.dev.LREG_STRIDE, val,
                                       comment=' // Stride')

                        val = (out_offset[ll] - out_ignore[ll]) // 4
                        if not local_source:
                            # Configure SRAM write pointer -- write ptr is global
                            # (unless depth-wise w/o broadcast is used).
                            # Get offset to first available instance of the first used
                            # processor of the next layer.
                            if hw_operator[ll] != op.NONE \
                               and conv_groups[ll] > 1 and not broadcast_mode[ll]:
                                # First group used
                                first_group = ffs(processor_map[ll]) // tc.dev.P_NUMPRO
                                if group - first_group >= 0:
                                    # Target for first write in the group
                                    wptr = (group - first_group) * tc.dev.P_NUMPRO \
                                        + ffs(output_processor_map[ll])
                                    if group != first_group:
                                        # Correct for unused processors in the first group
                                        wptr -= ffs(processor_map[ll]) % tc.dev.P_NUMPRO

                                    val |= (wptr // tc.dev.P_SHARED) << tc.dev.WRITE_PTR_SHIFT
                                else:
                                    val = 0
                            else:
                                if hw_operator[ll] != op.NONE:
                                    instance = ffs(output_processor_map[ll]) & ~(tc.dev.P_SHARED-1)
                                elif (output_processor_map[ll] &
                                      2**tc.dev.P_NUMPRO - 1 << group*tc.dev.P_NUMPRO > 0):
                                    instance = ffs(output_processor_map[ll]
                                                   & 2**tc.dev.P_NUMPRO - 1
                                                   << group*tc.dev.P_NUMPRO) \
                                        & ~(tc.dev.P_SHARED-1)
                                else:
                                    instance = 0

                                val |= (instance % tc.dev.P_SHARED) * tc.dev.INSTANCE_SIZE \
                                    | (instance // tc.dev.P_SHARED) << tc.dev.WRITE_PTR_SHIFT
                        else:
                            # FIXME: No test currently sets local_souce, so this code is suspect
                            instance = ffs(output_processor_map[ll] >> group * tc.dev.P_SHARED) \
                                   & ~(tc.dev.P_SHARED-1)
                            val |= (instance + group * tc.dev.P_SHARED) * tc.dev.INSTANCE_SIZE
                        assert val < 2**tc.dev.MAX_PTR_BITS
                        apb.write_lreg(group, r * layers + ll, tc.dev.LREG_WPTR_BASE, val,
                                       comment=' // SRAM write ptr')

                        # Write Pointer Timeslot Offset Register
                        # Used for 1x1 convolution, and pooling without convolution
                        val = 0
                        if hw_operator[ll] in [op.CONV2D, op.LINEAR]:
                            if hw_kernel_size[ll] == [1, 1] and conv_groups[ll] == 1:
                                val = 1
                            elif conv_groups[ll] > 1 and not broadcast_mode[ll]:
                                val = tc.dev.INSTANCE_SIZE * 4
                        elif hw_operator[ll] == op.NONE:
                            if popcount(processor_map[ll]) > 4 \
                               or operands[ll] > 1 and in_expand[ll] > 1:
                                val = tc.dev.INSTANCE_SIZE * 4
                            else:
                                val = tc.dev.INSTANCE_SIZE
                        assert val < 2**tc.dev.MAX_PTR_BITS
                        apb.write_lreg(group, r * layers + ll, tc.dev.LREG_WPTR_TOFFS, val,
                                       comment=' // Write ptr time slot offs')

                        if hw_operator[ll] != op.NONE:
                            # [15:0] Write Pointer Mask Offset Register
                            val = 1 << tc.dev.WRITE_PTR_SHIFT
                            apb.write_lreg(group, r * layers + ll, tc.dev.LREG_WPTR_MOFFS, val,
                                           comment=' // Write ptr mask offs')

                        # [15:0] Write Pointer Multi-Pass Channel Offset Register
                        val = 0
                        if out_expand[ll] > 1:
                            val = (output_width[ll] // 8) * (write_gap[ll] + 1)
                        apb.write_lreg(group, r * layers + ll, tc.dev.LREG_WPTR_CHOFFS, val,
                                       comment=' // Write ptr multi-pass channel offs')

                        # Configure sram read ptr count -- read ptr is local
                        # Source address must match write pointer of previous layer (minus global
                        # offset)
                        apb.write_lreg(group, r * layers + ll, tc.dev.LREG_RPTR_BASE,
                                       in_offset[ll] // 4,
                                       comment=' // SRAM read ptr')

                        # Configure per-layer control
                        val = (0x200 if activation[ll] == op.ACT_RELU else 0) | \
                              (0x100 if not pool_average[ll] else 0) | \
                              (0x80 if pool[ll][0] > 1 or pool[ll][1] > 1 else 0) | \
                              (0x40 if big_data[ll] else 0) | \
                              (0x20)
                        if not local_source:
                            val |= 1 << 11
                        if conv_groups[ll] > 1 and broadcast_mode[ll]:
                            val |= 1 << 29

                        if output_width[ll] != 8:
                            val |= 1 << 16

                        if (ll != start_layer or not fast_fifo_quad) \
                           and hw_operator[ll] != op.NONE and group == groups_used[0] \
                           and conv_groups[ll] == 1:
                            # Set external source for other active processing groups (can be
                            # zero if no other groups are processing). Do not set the bit
                            # corresponding to this group (e.g., if group == 0, do not set bit 12)
                            sources = 0
                            for t in range(groups_used[0]+1, tc.dev.P_NUMGROUPS):
                                # See if any processors other than this one are operating
                                # and set the cnnsiena bit if true
                                if (processor_map[ll] >> (t * tc.dev.P_NUMPRO)) \
                                   % 2**tc.dev.P_NUMPRO:
                                    sources |= 1 << t

                            # Also set cnnsiena if we get the bias from that group
                            if bias_group[ll] is not None and bias_group[ll] != group:
                                sources |= 1 << bias_group[ll]
                            val |= sources << 12

                        if rd_ahead[ll] and hasattr(tc.dev, 'RD_AHEAD_OFFS'):
                            val |= 1 << tc.dev.RD_AHEAD_OFFS

                        if hasattr(tc.dev, 'CPRIME_MAX_OFFS') and hw_operator[ll] != op.NONE:
                            val |= hw_kernel_size[ll][0] - 1 << tc.dev.RPRIME_MAX_OFFS
                            val |= hw_kernel_size[ll][1] - 1 << tc.dev.CPRIME_MAX_OFFS

                        if rd_ahead[ll] and hasattr(tc.dev, 'SHIFT_CNT_OFFS'):
                            val |= ((in_expand[ll] - 1) // 4 if tcalc[ll] else in_expand[ll] - 1) \
                                << tc.dev.SHIFT_CNT_OFFS

                        if bypass[ll]:
                            val |= 1 << 30

                        apb.write_lreg(group, r * layers + ll, tc.dev.LREG_LCTL, val,
                                       comment=' // Layer control')

                        flatten_prod = 0
                        if flatten[ll]:
                            # Store all bits, top programmed in post processing register
                            flatten_prod = \
                                in_expand[ll] * hw_pooled_dim[ll][0] * hw_pooled_dim[ll][1] - 1
                            in_exp = flatten_prod & 0x0f  # Lower 4 bits only
                        elif hw_operator[ll] == op.NONE and emulate_eltwise[ll]:
                            in_exp = 0
                        else:
                            in_exp = in_expand[ll] - 1

                        assert in_exp < 2**4  # Cannot have more than 4 bits

                        quant = abs(quantization[ll]) if not bypass[ll] else 8
                        val = (fls(output_processor_map[ll])
                               - (ffs(output_processor_map[ll]) & ~(tc.dev.P_SHARED-1))) \
                            * quant << tc.dev.XPCH_MAX_OFFS | in_exp
                        if hw_operator[ll] != op.NONE:
                            wptr_skip = out_expand[ll] * (write_gap[ll] + 1) - 1
                        else:
                            wptr_skip = write_gap[ll]
                        assert wptr_skip < 2**tc.dev.MAX_WPTRINC_BITS
                        val |= wptr_skip << 4

                        apb.write_lreg(group, r * layers + ll, tc.dev.LREG_LCTL2, val,
                                       comment=' // Layer control 2')

                        # Configure mask start and end addresses
                        # Every mask memory starts from the same offset for all processors
                        oned_sad = 0
                        if hw_operator[ll] != op.NONE:
                            # FIXME: bypass corner cases
                            kc = kern_count[ll] if not bypass[ll] \
                                else output_chan[ll] // conv_groups[ll]
                            kl = (kc - 1) * quant

                            if ll == start_layer and calcx4[ll]:
                                # FIXME: Handle fast_fifo_quad and calcx4
                                if calcx4[ll]:
                                    kl += quant
                                kl = (kl + 3) // 4
                                if calcx4[ll]:
                                    kl -= quant
                            koffs, oned_sad = divmod(9 * kern_offs[ll],
                                                     hw_kernel_size[ll][0] * hw_kernel_size[ll][1])
                            if calcx4[ll]:
                                koffs = kernels.calcx4_index(koffs)
                            koffs *= 8
                        else:
                            kl = koffs = 0

                        if hasattr(tc.dev, 'LREG_MCNT1'):
                            if hw_operator[ll] != op.NONE:
                                assert koffs < 2**19
                                assert kl + koffs < 2**19
                                apb.write_lreg(group, r * layers + ll, tc.dev.LREG_MCNT1,
                                               kl + koffs,
                                               comment=' // Mask count')
                                apb.write_lreg(group, r * layers + ll, tc.dev.LREG_MCNT2, koffs,
                                               comment=' // Mask offset')
                            else:
                                val = (out_expand[ll] - 1) * 8
                                assert val < 2**19
                                apb.write_lreg(group, r * layers + ll, tc.dev.LREG_MCNT2, val,
                                               comment=' // Mask offset')
                        else:
                            if hw_operator[ll] != op.NONE:
                                assert koffs < 2**16
                                assert kl + koffs < 2**16
                                # kern_offs is always bytes
                                val = \
                                    koffs << tc.dev.MCNT_SAD_OFFS | kl + \
                                    koffs << tc.dev.MCNT_MAX_OFFS
                            elif emulate_eltwise[ll]:
                                val = 0
                            else:
                                val = (out_expand[ll] - 1) * 8
                                assert val < 2**16
                            apb.write_lreg(group, r * layers + ll, tc.dev.LREG_MCNT, val,
                                           comment=' // Mask offset and count')

                        if hasattr(tc.dev, 'LREG_OCHAN'):
                            if bypass[ll]:
                                val = output_chan[ll] - 1
                            elif hw_operator[ll] != op.NONE and conv_groups[ll] == 1:
                                val = kern_ochan[ll] - 1
                                if calcx4[ll]:
                                    val //= 4
                            elif conv_groups[ll] > 1:
                                val = (tscnt_max + 1) * in_expand[ll] - 1
                            else:
                                val = tscnt_max
                            apb.write_lreg(group, r * layers + ll, tc.dev.LREG_OCHAN, val,
                                           comment=' // Output channel count')

                        val = tscnt_max
                        assert 0 <= val < 2**4
                        if hw_operator[ll] == op.CONV1D:
                            val |= hw_kernel_size[ll][0] << 8 | 1 << 12
                            assert hw_kernel_size[ll][0] < 2**4
                        elif (hw_operator[ll] in [op.CONV2D, op.LINEAR]
                              and hw_kernel_size[ll] == [1, 1]
                              or hw_operator[ll] == op.NONE and operands[ll] == 1):
                            val |= 1 << 8
                        if operands[ll] > 1:
                            val |= \
                                1 << 13 | op.eltwise_fn(eltwise[ll]) << 14 | operands[ll] - 1 << 18
                            if (pool[ll][0] > 1 or pool[ll][1] > 1) \
                               and pool_first[ll]:
                                val |= 1 << 16
                            if hw_operator[ll] != op.NONE:  # CONV2D, LINEAR, CONVTRANSPOSE2D
                                val |= 1 << 17
                        assert 0 <= oned_sad < 2**4
                        val |= oned_sad << 4

                        apb.write_lreg(group, r * layers + ll, tc.dev.LREG_ONED, val,
                                       comment=' // 1D')

                        # Configure tram pointer max
                        if hw_operator[ll] == op.CONV1D or \
                           hw_operator[ll] in [op.CONV2D, op.LINEAR] \
                           and hw_kernel_size[ll] == [1, 1] \
                           and (ll == 0 or not streaming[ll]):
                            if flatten_prod >= 2**4:
                                assert flatten_prod < 2**16
                                val = flatten_prod << 16 | (2 * flatten_prod + 1)
                            else:
                                val = 0
                        else:
                            val = tram_max[ll] - 1
                            assert val < 2**16
                            if ll > 0 and streaming[ll]:
                                prev_max = np.multiply(tram_max[:ll], in_expand[:ll]).sum()
                                assert prev_max < 2**tc.dev.MAX_TPTR_BITS
                                val += prev_max
                                assert val < 2**16
                                val |= prev_max << 16
                        apb.write_lreg(group, r * layers + ll, tc.dev.LREG_TPTR, val,
                                       comment=' // TRAM ptr max')

                        # Compensate for the smaller weights by adjusting the output shift
                        if abs(quantization[ll]) == 1:
                            val = 1 << 22
                        elif quantization[ll] == 2:
                            val = 2 << 22
                        elif quantization[ll] == 4:
                            val = 3 << 22
                        else:
                            assert quantization[ll] in [0, 8]
                            val = 0  # Do not shift
                        # Scale Control - bit 4 determines shift direction (1>>,0<<),
                        # bits[3:0] determine magnitude
                        assert hw_operator[ll] != op.NONE or output_shift[ll] == 0
                        if output_shift[ll] < 0:
                            val |= (-output_shift[ll] | 2**4) << 13
                        else:
                            val |= output_shift[ll] << 13

                        # [24] ts_ena
                        # [25] onexone_ena

                        if conv_groups[ll] == 1 and group == bias_group[ll]:
                            # Enable bias only for one group
                            offs = bias_offs[ll][group]
                            if calcx4[ll]:
                                offs //= 4
                            assert offs < 2**12
                            val |= 1 << 12 | offs
                        elif bias_offs[ll][group] is not None and (
                            conv_groups[ll] > 1 or fast_fifo_quad and ll == 0
                        ):
                            # Enable bias for all groups
                            offs = bias_offs[ll][group]
                            if broadcast_mode[ll]:
                                offs //= 4
                            assert offs < 2**12
                            val |= 1 << 12 | offs

                        if hw_operator[ll] == op.NONE:
                            if operands[ll] == 1:
                                val |= 3 << 24
                            else:
                                val |= 1 << 24

                        if activation[ll] == op.ACT_ABS:
                            val |= 1 << 26

                        if flatten_prod >= 2**4:
                            val |= 1 << 27 | (flatten_prod >> 4) << 18  # flatten_ena, xpmp_cnt

                        if hw_operator[ll] == op.CONVTRANSPOSE2D:
                            val |= 1 << 28

                        if conv_groups[ll] > 1:
                            val |= 1 << 30 | 1 << 24  # depthwise_ena, ts_ena

                        if calcx4[ll]:
                            val |= 1 << 29

                        if tcalc[ll]:
                            val |= 1 << 31

                        apb.write_lreg(group, r * layers + ll, tc.dev.LREG_POST, val,
                                       comment=' // Post processing register')

                        # Configure mask and processor enables
                        # Enable at most 16 processors and masks
                        val = (processor_map[ll] >> group*tc.dev.P_NUMPRO) % 2**tc.dev.P_NUMPRO
                        if hw_operator[ll] != op.NONE and not bypass[ll]:
                            val = val << 16 | val  # Mask enables
                        apb.write_lreg(group, r * layers + ll, tc.dev.LREG_ENA, val,
                                       comment=' // Mask and processor enables')

                        delta1 = delta2 = stream_start = invol = 0
                        last_layer = False
                        if not tc.dev.REQUIRE_NEW_STREAMING:
                            if ll == start_layer and fifo:
                                # Start: 1
                                if override_start is not None:
                                    stream_start = override_start
                                elif streaming[ll]:
                                    stream_start = (pool[ll][0] - 1) * hw_input_dim[ll][1] \
                                        + pool[ll][1]
                                else:
                                    stream_start = hw_input_dim[ll][0] * hw_input_dim[ll][1]
                                    if big_data[ll]:
                                        stream_start = (stream_start + 3) // 4
                                stream_start *= pool[ll][0]

                                if streaming[ll]:
                                    # Delta 1: This layer's pooling stride
                                    if override_delta1 is not None:
                                        delta1 = override_delta1
                                    else:
                                        delta1 = (pool_stride[ll][1] - 1) * operands[ll]
                                    if override_delta2 is not None:
                                        delta2 = override_delta2
                                    else:
                                        delta2 = (pool[ll][0] - 1) * hw_input_dim[ll][1] \
                                            * operands[ll]

                            elif ll > start_layer and streaming[ll]:
                                # Start: Prior layer's padded pooled row width * prior layer's
                                # kernel height + prior layer's kernel width + prior layer's pad
                                stream_start = (hw_pooled_dim[prev_sequence[ll]][1]
                                                + 2 * hw_padding[prev_sequence[ll]][1]) \
                                    * (hw_kernel_size[prev_sequence[ll]][0] - 1
                                       + pool[ll][0] - 1) \
                                    + hw_kernel_size[prev_sequence[ll]][1] - 1 + pool[ll][1] \
                                    + increase_start

                                # Delta 1: This layer's pooling stride
                                delta1 = pool_stride[ll][1] * operands[ll] + increase_delta1

                                # Delta 2: (This layer's pooling - 1) * full prior layer's padded
                                # rows + prior layer's pad
                                delta2 = (pool_stride[ll][0] - 1) \
                                    * (hw_pooled_dim[prev_sequence[ll]][1]
                                        + 2 * hw_padding[prev_sequence[ll]][1]) \
                                    + pool[ll][1] * operands[ll] + increase_delta2
                        else:
                            # MAX78002
                            # =IF(Pad=0,Stride,Pad)
                            row_prim = hw_padding[ll][1] or pool_stride[ll][0]
                            # =IF((Row_Pool*Row_Dilation_Stride)>Stride,
                            #     Row_Pool*Row_Dilation_Stride,Stride)
                            row_inc = max(pool[ll][0] * pool_dilation[ll][0], pool_stride[ll][0])
                            # =IF((Col_Pool*Col_Dilation_Stride)>Stride,
                            #     (Col_Pool*Col_Dilation_Stride),Stride)
                            col_inc = max(pool[ll][1] * pool_dilation[ll][1], pool_stride[ll][1])

                            if streaming[ll]:
                                if ll == final_layer or not streaming[next_sequence[ll]]:
                                    last_layer = True
                                if debug_new_streaming and ll > 0:
                                    # Cols_Stride=ROUNDDOWN(Cols/Col_Inc,0)
                                    # =IF(Cols_Stride*Stride+Pool>Cols,Cols_Stride-1,Cols_Stride)
                                    effective_cols = hw_input_dim[ll][1] // col_inc
                                    if effective_cols * pool_stride[ll][1] + pool[ll][1] > \
                                       hw_input_dim[ll][1]:
                                        effective_cols -= 1
                                    # =IF(Col_Inc=1,0,Cols-(Effective_Cols*Col_Inc))
                                    col_adjust = 0 if col_inc == 1 \
                                        else hw_input_dim[ll][1] - effective_cols * col_inc
                                else:
                                    # =IF(((ROUNDDOWN(Cols/Stride,0)*Stride)+(Pool-1))>Cols,
                                    #     (ROUNDDOWN((Cols/Stride),0))-1,
                                    #     (ROUNDDOWN((Cols/Stride),0)))
                                    effective_cols = hw_input_dim[ll][1] // pool_stride[ll][1]
                                    if effective_cols * pool_stride[ll][1] + pool[ll][1] - 1 > \
                                       hw_input_dim[ll][1]:
                                        effective_cols -= 1
                                    # =(Cols-(Effective_Cols*Col_Inc))
                                    col_adjust = hw_input_dim[ll][1] - effective_cols * col_inc

                            # Prefill
                            if ll == start_layer and override_start is not None:
                                stream_start_hwc = stream_start = override_start
                            elif ll == start_layer and fifo:
                                if streaming[ll]:
                                    # =IF(AND(Stride=1,Row_Pool=1),Col_Inc,
                                    #     (((Row_Inc-1)*(Cols+(Pad*2)))+Col_Inc))
                                    if pool_stride[ll][0] == 1 and pool[ll][0] == 1:
                                        stream_start = col_inc
                                    else:
                                        stream_start = (row_inc - 1) * \
                                            (hw_input_dim[ll][1]
                                             + 2 * hw_padding[ll][1]) + col_inc
                                else:  # fifo only
                                    stream_start = hw_input_dim[ll][0] * hw_input_dim[ll][1]
                                stream_start_hwc = stream_start
                                if big_data[ll]:
                                    stream_start = (stream_start + 3) // 4
                            elif ll > start_layer and streaming[ll]:
                                if debug_new_streaming and ll > 0:
                                    # =(Row_Prim*(Cols+(Pad*2)))
                                    #   +(((Row_Inc*(Cols+(Pad*2)))
                                    #   +(Pad+(2*Col_Inc)))*Elementwise)
                                    #   +(Read_Ahead*Stride)
                                    # if last_layer:
                                    #   += (Col_Adj*Stride)
                                    stream_start = \
                                        row_prim * \
                                        (hw_input_dim[ll][1] + 2 * hw_padding[ll][1]) \
                                        + (row_inc * (hw_input_dim[ll][1]
                                                      + 2 * hw_padding[ll][1])
                                           + hw_padding[ll][1] + 2 * col_inc) * operands[ll]
                                else:
                                    # =(Pad*(Cols+(Pad*2)))
                                    #   +(((Row_Inc*(Cols+(Pad*2)))
                                    #   +(Pad+(2*Col_Inc)))*Elementwise)
                                    #   +(Read_Ahead*Stride)
                                    # if last_layer:
                                    #   += (Col_Adj*Stride)
                                    stream_start = \
                                        hw_padding[ll][1] * \
                                        (hw_input_dim[ll][1] + 2 * hw_padding[ll][1]) \
                                        + (row_inc * (hw_input_dim[ll][1]
                                                      + 2 * hw_padding[ll][1])
                                           + hw_padding[ll][1] + 2 * col_inc) * operands[ll]
                                if rd_ahead[ll]:
                                    stream_start += pool_stride[ll][1]
                                if last_layer and debug_new_streaming:
                                    stream_start += col_adjust * pool_stride[ll][1]
                                stream_start_hwc = stream_start
                                if big_data[ll]:
                                    # =(ROUNDUP(Prefill/4,0))
                                    stream_start = (stream_start + 3) // 4

                            # Delta 1 Count, Delta 2 Count
                            if streaming[ll]:
                                # =IF((Cols-(Effective_Cols*Stride))<0,0,
                                #      (Cols-(Effective_Cols*Stride)))
                                skipped_cols = max(
                                    0,
                                    (hw_input_dim[ll][1] - (effective_cols * pool_stride[ll][1]))
                                )

                                if ll == start_layer:
                                    if override_delta1 is not None:
                                        delta1 = override_delta1
                                    else:
                                        # =(Stride*Elementwise)-1
                                        delta1 = pool_stride[ll][1] * operands[ll] - 1
                                        if big_data[ll]:
                                            # =(ROUNDUP(Delta1_0/4,0))
                                            delta1 = (delta1 + 3) // 4
                                        if pipeline and delta1 > 0:
                                            delta1 += 1

                                    if override_delta2 is not None:
                                        delta2 = override_delta2
                                    else:
                                        if debug_new_streaming and ll > 0:
                                            # =IF(Stride=1,Delta1_0+Col_Adj,
                                            #     (((Stride-1)*Cols))+Col_Adj)
                                            if pool_stride[ll][0] == 1:
                                                delta2 = delta1 + col_adjust
                                            else:
                                                delta2 = (pool_stride[ll][0] - 1) \
                                                    * hw_input_dim[ll][1] + col_adjust
                                        else:
                                            # =IF(Stride=1,Delta1_0+Skipped_Cols,
                                            #            (((Stride-1)*Cols))+Skipped_Cols
                                            #              +(Col_Pool-1))
                                            if pool_stride[ll][0] == 1:
                                                delta2 = delta1 + skipped_cols
                                            else:
                                                delta2 = (pool_stride[ll][0] - 1) \
                                                    * hw_input_dim[ll][1] \
                                                    + skipped_cols + pool[ll][1] - 1
                                        if big_data[ll]:
                                            # =(ROUNDUP(Delta2_0/4,0))
                                            delta2 = (delta2 + 3) // 4
                                        if pipeline and delta2 > 0:
                                            delta2 += 1
                                else:  # != start_layer
                                    # =Stride*Elementwise
                                    delta1 = pool_stride[ll][1] * operands[ll]
                                    if big_data[ll]:
                                        # =(ROUNDUP(Delta1/4,0))
                                        delta1 = (delta1 + 3) // 4
                                    delta1 += increase_delta1

                                    if debug_new_streaming and ll > 0:
                                        # =IF(Stride=1,Delta1+Col_Adj,((Stride-1)*Cols)+Col_Adj)
                                        if pool_stride[ll][0] == 1:
                                            delta2 = delta1 + col_adjust
                                        else:
                                            delta2 = (pool_stride[ll][0] - 1) \
                                                * hw_input_dim[ll][1] \
                                                + col_adjust
                                    else:
                                        # =IF(Stride=1,Delta1+Skipped_Cols,
                                        #     ((Stride-1)*Cols)+Skipped_Cols)
                                        if pool_stride[ll][0] == 1:
                                            delta2 = delta1 + skipped_cols
                                        else:
                                            delta2 = (pool_stride[ll][0] - 1) \
                                                * hw_input_dim[ll][1] \
                                                + skipped_cols
                                    if big_data[ll]:
                                        # =(ROUNDUP(Delta2/4,0))
                                        delta2 = (delta2 + 3) // 4
                                    delta2 += increase_delta2

                        # strm_invol[3:0]: Per stream invol offset - based on stream count
                        if ll > start_layer and streaming[ll]:
                            invol = sum(in_expand_invol[:ll])

                        assert stream_start < 2**tc.dev.MAX_ISVAL_BITS
                        val = stream_start
                        if state.fifo_go and ll == start_layer:
                            val |= 1 << 25
                        apb.write_lreg(group, r * layers + ll, tc.dev.LREG_STREAM1, val,
                                       comment=' // Stream processing start')

                        assert invol < 2**4, \
                            f'Layer {ll} invol ({invol:04x}) exceeds supported range.'
                        assert delta1 < 2**5
                        assert delta2 < 2**tc.dev.MAX_DSVAL2_BITS
                        val = delta2 << 16 | delta1 << 4 | invol
                        apb.write_lreg(group, r * layers + ll, tc.dev.LREG_STREAM2, val,
                                       comment=' // Stream processing delta')

                        if fifo and streaming[ll]:
                            if ll == start_layer and override_rollover is not None:
                                val = override_rollover
                            elif not tc.dev.REQUIRE_NEW_STREAMING:
                                if big_data[ll]:
                                    # FIXME: stream_start + max(stride[ll][1], pool_stride[ll][1])
                                    val = 12
                                else:
                                    val = stream_start + (pool[ll][0] - 1) * hw_input_dim[ll][1] \
                                        + max(stride[ll][1], pool_stride[ll][1], pool[ll][1])
                                # Rollover must be multiple of multi-pass:
                                rem = val % in_expand[ll]
                                if rem > 0:
                                    val = val + in_expand[ll] - rem
                            else:
                                # MAX78002 - Buffer
                                if ll == start_layer:
                                    # =Prefill0 + Col_Inc
                                    val = stream_start_hwc + col_inc
                                    if big_data[ll]:
                                        # =Buffer0*4*Stride
                                        val *= pool_stride[ll][1] * 4
                                else:
                                    # =(MROUND(Prefill+((Passes-1)*((Row_Inc*Cols)+Pad+Col_Inc))
                                    #   +Col_Inc,Passes))
                                    val = stream_start_hwc \
                                        + (in_expand[ll] - 1) * (row_inc * hw_input_dim[ll][1]
                                                                 + hw_padding[ll][1]
                                                                 + col_inc) \
                                        + col_inc
                                    val += (in_expand[ll] - val % in_expand[ll]) % in_expand[ll]
                                    if big_data[ll]:
                                        # =(ROUNDUP(Buffer/4,0))
                                        val = (val + 3) // 4

                            assert val < 2**tc.dev.MAX_FBUF_BITS

                            # Check rollover vs available data memory
                            if in_offset[ll] < out_offset[ll] - out_ignore[ll]:
                                if in_offset[ll] + val * 4 >= out_offset[ll] - out_ignore[ll]:
                                    eprint(f'Layer {ll}: Overlapping input and output: '
                                           f'in_offset 0x{in_offset[ll]:08x} + '
                                           f'rollover 0x{val:08x} * 4 >= '
                                           f'out_offset 0x{out_offset[ll]:08x} - '
                                           f'out_ignore 0x{out_ignore[ll]:08x}.',
                                           error=not no_error_stop)
                            else:
                                if out_offset[ll] + val * 4 >= in_offset[ll]:
                                    eprint(f'Layer {ll}: Overlapping input and output: '
                                           f'out_offset 0x{out_offset[ll]:08x} + '
                                           f'rollover 0x{val:08x} * 4 >= '
                                           f'in_offset 0x{in_offset[ll]:08x}.',
                                           error=not no_error_stop)
                                if ll == terminating_layer:
                                    osize = output_dim[ll][0] * output_dim[ll][1] + out_pad[ll]
                                    if out_offset[ll] + osize * out_expand[ll] * 4 >= \
                                       in_offset[ll]:
                                        eprint(f'Layer {ll}: Overlapping input and output: '
                                               f'out_offset 0x{out_offset[ll]:08x} + '
                                               f'output of size {osize} ({output_dim_str[ll]}) '
                                               f'* {out_expand[ll]} * 4 >= '
                                               f'in_offset 0x{in_offset[ll]:08x}.',
                                               error=not no_error_stop)
                            if in_offset[ll] + val * 4 >= tc.dev.INSTANCE_WIDTH \
                               * tc.dev.P_SHARED * 4:
                                eprint('Input plus rollover exceeds instance size: '
                                       f'in_offset 0x{in_offset[ll]:08x}, '
                                       f'out_offset 0x{out_offset[ll]:08x}, '
                                       f'rollover 0x{val:08x}, '
                                       f'instance size 0x{tc.dev.INSTANCE_WIDTH*4:08x}.',
                                       error=not no_error_stop)

                            # Check streaming buffers for overlap across all streaming layers and
                            # the data memories used by the processors in the streaming layers, as
                            # well as the output of the last streaming layer.
                            dmap = tc.dev.datamem_map(processor_map[ll],
                                                      fast_fifo_quad and ll == 0)
                            stream_buf[ll] = (in_offset[ll], in_offset[ll] + val * 4, dmap)
                            for pl in range(ll):
                                if stream_buf[pl] is None:
                                    continue
                                if stream_buf[pl][2] & dmap != 0 \
                                   and overlap(stream_buf[ll], stream_buf[pl]):
                                    eprint(f'Streaming buffer for layer {ll} '
                                           f'({stream_buf[ll][0]:04x}-{stream_buf[ll][1]:04x}, '
                                           f'processors {processor_map[ll]:016x}) '
                                           f'overlaps layer {pl} '
                                           f'({stream_buf[pl][0]:04x}-{stream_buf[pl][1]:04x}, ',
                                           f'processors {processor_map[pl]:016x}).',
                                           error=not overwrite_ok)
                                if rd_ahead[ll] \
                                   and tc.dev.datainstance_from_offs(stream_buf[ll][0]) \
                                   == tc.dev.datainstance_from_offs(stream_buf[pl][0]):
                                    eprint(f'Layer {ll}: In streaming mode with read-ahead, all '
                                           'streaming read-ahead layers must use separate memory '
                                           f'instances. Layer {ll} conflicts with layer {pl}; '
                                           'both use instance '
                                           f'{tc.dev.datainstance_from_offs(stream_buf[pl][0])}.')

                            if ll == final_layer or not streaming[next_sequence[ll]]:
                                dmap = tc.dev.datamem_map(output_processor_map[ll])
                                for pl in range(ll + 1):
                                    if stream_buf[pl] is None:
                                        continue
                                    if stream_buf[pl][2] & dmap != 0 \
                                       and overlap((out_offset[ll], out_offset[ll]
                                                   + (output_dim[ll][0] * output_dim[ll][1]
                                                      + out_pad[ll]) * 4
                                                   * output_width[ll] // 8), stream_buf[pl]):
                                        eprint(f'Output for layer {ll} '
                                               f'({out_offset[ll]:04x}-{stream_buf[ll][1]:04x}, '
                                               'output processors '
                                               f'{output_processor_map[ll]:016x}) '
                                               f'overlaps streaming buffer for layer {pl} '
                                               f'({stream_buf[pl][0]:04x}-{stream_buf[pl][1]:04x}'
                                               f', processors {processor_map[pl]:016x}).',
                                               error=not overwrite_ok)

                            apb.write_lreg(group, r * layers + ll, tc.dev.LREG_FMAX, val,
                                           comment=' // Rollover')

                        # In read-ahead mode, ensure that input and output use separate
                        # instances. First, check the start addresses, then the end addresses.
                        if rd_ahead[ll]:
                            in_instance = (
                                tc.dev.datainstance_from_offs(in_offset[ll]),
                                tc.dev.datainstance_from_offs(in_offset[ll] + 4 * operands[ll]
                                                              * in_expand[ll] * hw_input_dim[ll][0]
                                                              * hw_input_dim[ll][1])
                            )
                            out_instance = (
                                tc.dev.datainstance_from_offs(out_offset[ll]),
                                tc.dev.datainstance_from_offs(out_offset[ll] + 4 * out_expand[ll]
                                                              * (output_dim[ll][0]
                                                                 * output_dim[ll][1]
                                                                 + out_pad[ll]))
                            )
                            if in_instance[0] == out_instance[0] \
                               or in_instance[1] == out_instance[1]:
                                eprint(f'Layer {ll}: Input and output cannot use the same data '
                                       'memory instances in read-ahead mode. '
                                       f'in_offset: {in_offset[ll]:04x}/instance(s) '
                                       f'{in_instance}, out_offset: {out_offset[ll]:04x}/'
                                       f'instance(s) {out_instance}.')

                        if ll == start_layer and fifo:
                            val = hw_input_dim[ll][0] * hw_input_dim[ll][1]
                            if big_data[ll]:
                                val = (val + 3) // 4
                            assert val < 2**tc.dev.MAX_IFRM_BITS
                            apb.write_ctl(group, tc.dev.REG_IFRM, val,
                                          comment=' // Input frame size')

                        apb.output('\n', embedded_code)  # End of group

            if zero_unused:
                for r in range(repeat_layers):
                    for ll in range(first_layer_used, layers, tc.dev.MAX_LAYERS):
                        for _, group in enumerate(groups_used):
                            for reg in range(tc.dev.MAX_LREG+1):
                                if reg == tc.dev.LREG_RFU:  # Register 2 not implemented
                                    continue
                                apb.write_lreg(group, r * layers + ll, reg, 0,
                                               force_write=True,
                                               comment=f' // Zero unused layer {ll} registers')
                    if hasattr(tc.dev, 'MIN_STREAM_LREG'):
                        for ll in range(first_layer_used, layers, tc.dev.MAX_STREAM_LAYERS):
                            for _, group in enumerate(groups_used):
                                for reg in range(tc.dev.MIN_STREAM_LREG, tc.dev.MAX_STREAM_LREG+1,
                                                 tc.dev.MAX_STREAM_LAYERS):
                                    apb.write_lreg(group, r * layers + ll, reg, 0,
                                                   force_write=True,
                                                   comment=f' // Zero unused layer {ll} registers')

            if snoop is not None:
                apb.output('  // Configure conditional execution\n', embedded_code)
                for _, group in enumerate(groups_used):
                    assert len(snoop) == 32
                    apb.write_ctl(group, tc.dev.REG_SNP1_A1, snoop[0],
                                  comment=' // Address snoop 1 register 1')
                    apb.write_ctl(group, tc.dev.REG_SNP1_A2, snoop[1],
                                  comment=' // Address snoop 1 register 2')
                    apb.write_ctl(group, tc.dev.REG_SNP1_D1, snoop[2],
                                  comment=' // Data snoop 1 register 1')
                    apb.write_ctl(group, tc.dev.REG_SNP1_D2, snoop[3],
                                  comment=' // Data snoop 1 register 2')
                    apb.write_ctl(group, tc.dev.REG_SNP1_X1, snoop[4],
                                  comment=' // Count snoop 1 register 1')
                    apb.write_ctl(group, tc.dev.REG_SNP1_X2, snoop[5],
                                  comment=' // Count snoop 1 register 2')
                    apb.write_ctl(group, tc.dev.REG_SNP1_C1, snoop[6],
                                  comment=' // Snoop 1 control register 1')
                    apb.write_ctl(group, tc.dev.REG_SNP1_C2, snoop[7],
                                  comment=' // Snoop 1 control register 2')
                    apb.write_ctl(group, tc.dev.REG_SNP1_ACC, snoop[8],
                                  comment=' // Snoop 1 data accumulator')
                    apb.write_ctl(group, tc.dev.REG_SNP1_HIT, snoop[9],
                                  comment=' // Snoop 1 match hit accumulator')
                    apb.write_ctl(group, tc.dev.REG_SNP1_MAX, snoop[10],
                                  comment=' // Snoop 1 match max accumulator')
                    apb.write_ctl(group, tc.dev.REG_SNP1_AM, snoop[11],
                                  comment=' // Snoop 1 match address register')
                    apb.write_ctl(group, tc.dev.REG_SNP2_A1, snoop[12],
                                  comment=' // Address snoop 2 register 1')
                    apb.write_ctl(group, tc.dev.REG_SNP2_A2, snoop[13],
                                  comment=' // Address snoop 2 register 2')
                    apb.write_ctl(group, tc.dev.REG_SNP2_D1, snoop[14],
                                  comment=' // Data snoop 2 register 1')
                    apb.write_ctl(group, tc.dev.REG_SNP2_D2, snoop[15],
                                  comment=' // Data snoop 2 register 2')
                    apb.write_ctl(group, tc.dev.REG_SNP2_X1, snoop[16],
                                  comment=' // Count snoop 2 register 1')
                    apb.write_ctl(group, tc.dev.REG_SNP2_X2, snoop[17],
                                  comment=' // Count snoop 2 register 2')
                    apb.write_ctl(group, tc.dev.REG_SNP2_C1, snoop[18],
                                  comment=' // Snoop 2 control register 1')
                    apb.write_ctl(group, tc.dev.REG_SNP2_C2, snoop[19],
                                  comment=' // Snoop 2 control register 2')
                    apb.write_ctl(group, tc.dev.REG_SNP2_ACC, snoop[20],
                                  comment=' // Snoop 2 data accumulator')
                    apb.write_ctl(group, tc.dev.REG_SNP2_HIT, snoop[21],
                                  comment=' // Snoop 2 match hit accumulator')
                    apb.write_ctl(group, tc.dev.REG_SNP2_MAX, snoop[22],
                                  comment=' // Snoop 2 match max accumulator')
                    apb.write_ctl(group, tc.dev.REG_SNP2_AM, snoop[23],
                                  comment=' // Snoop 2 match address register')

                    apb.output('\n', embedded_code)

            if not fifo:
                # Load data memory
                if embedded_code or compact_data or input_csv:
                    # Do the actual code generation later
                    if not embedded_code:
                        apb.output('\n  load_input(); // Load data input\n\n')
                else:
                    load.load(
                        embedded_code,
                        apb,
                        big_data[start_layer],
                        processor_map_0,
                        in_offset[start_layer],
                        [input_chan[start_layer],
                         input_dim[start_layer][0],
                         input_dim[start_layer][1]],
                        in_expand[start_layer],
                        operands[start_layer],
                        in_expand_thresh[start_layer],
                        data,
                        hw_padding[start_layer],
                        csv_file=csv,
                    )

            if verbose:
                print('\nGlobal registers:')
                print('-----------------')

            # Configure the FIFOs when we're using them
            if fifo:
                apb.output('\n', embedded_code)

                # FIFO control
                if not fast_fifo:
                    val = 0x02 << 2 | 0x02 << 7 | tc.dev.FIFO_READY_SEL
                    if tc.dev.REQUIRE_FIFO_CPL:
                        val |= 1 << 11
                    for i in range(input_chan[start_layer]):
                        if processor_map_0 & 1 << (i % tc.dev.P_NUMGROUPS) * tc.dev.P_NUMPRO != 0:
                            val |= 1 << i % tc.dev.P_NUMGROUPS + 12
                    apb.write_fifo_ctl(tc.dev.FIFO_CTL, val,
                                       comment=' // FIFO control')
                else:
                    apb.write_fast_fifo_ctl(tc.dev.FAST_FIFO_IE, 0,
                                            comment=' // Fast FIFO interrupt enable')
                    val = 10 << 4  # Async, threshold 10
                    apb.write_fast_fifo_ctl(tc.dev.FAST_FIFO_CR, val,
                                            comment=' // Fast FIFO control')

            val = 1 << 14 if any(streaming) else 0
            if avg_pool_rounding:
                val |= 1 << 13
            if fifo:
                val |= 1 << 11
            if fifo and any(streaming):
                val |= 1 << 19
            val |= 1 << 3  # Enable clocks
            if mexpress:
                val |= 1 << 20
            if simple1b:
                val |= 1 << 21
            if binary_quantization:
                val |= 1 << 30
            if fast_fifo_quad:
                val |= 1 << 31  # Qupac bit
            if oneshot:
                val |= 1 << 8
            if ext_rdy:
                val |= 1 << 4
            if hasattr(tc.dev, 'CTL_PIPELINE_OFFS'):
                if not pipeline:
                    val |= 1 << tc.dev.CTL_PIPELINE_OFFS
                if streaming[start_layer] and big_data[start_layer]:
                    val |= 1 << 6
            if snoop is not None:
                val |= 1 << 7

            if embedded_code:
                apb.function_footer()
                apb.function_header(function='start')

            if embedded_code or tc.dev.MODERN_SIM:
                apb.output('  cnn_time = 0;\n\n', embedded_code)

            # Enable all needed groups except the first one
            rdy_sel = tc.dev.READY_SEL if not pipeline else tc.dev.PIPELINE_READY_SEL
            for _, group in enumerate(groups_used):
                # Turn on the FIFO for this group if it's being loaded
                if fifo and processor_map_0 & 0x0f << group * 16 != 0:
                    fval = 1 << 15
                    if fast_fifo:
                        fval |= 1 << 22
                    if fifo_group:
                        fval |= 1 << 23
                elif fifo:
                    fval = 1 << 15
                else:
                    fval = 0
                if group != groups_used[0]:
                    fval |= 0x01
                apb.write_ctl(group, tc.dev.REG_CTL, val | 0x800 | rdy_sel << 1
                              | fval | groups_used[0] << 9,
                              comment=f' // Enable group {group}')

            if powerdown:
                unused_groups = [group for group in list(range(tc.dev.P_NUMGROUPS))
                                 if group not in groups_used]
                val2 = 0
                for _, group in enumerate(unused_groups):
                    val2 |= 1 << 12 + group
                apb.write_fifo_ctl(tc.dev.AON_CTL, val2 | tc.dev.AON_READY_SEL,
                                   comment=' // AON control')

            if state.pll and not measure_energy:
                apb.select_clock('ITO', 'DIV1', 'Switch CNN clock to PLL (ITO)')
            if embedded_code:
                apb.output('\n#ifdef CNN_INFERENCE_TIMER\n'
                           '  MXC_TMR_SW_Start(CNN_INFERENCE_TIMER);\n'
                           '#endif\n\n', embedded_code)
                if not measure_energy:
                    apb.output('  CNN_START; // Allow capture of processing time\n', embedded_code)

            # Master control - go
            if fifo and processor_map_0 & 0x0f << groups_used[0] * 16 != 0:
                val |= 1 << 15
                if fast_fifo:
                    val |= 1 << 22
                if fifo_group:
                    val |= 1 << 23
            apb.write_ctl(groups_used[0], tc.dev.REG_CTL, val | rdy_sel << 1 | 0x01,
                          comment=f' // Master enable group {groups_used[0]}')

            if fifo:
                # Load data memory
                if embedded_code or compact_data or input_csv:
                    # Do the actual code generation later
                    if not embedded_code:
                        apb.output('\n  load_input(); // Load data input\n\n')
                else:
                    load.load(
                        False,
                        apb,
                        big_data[start_layer],
                        processor_map_0,
                        in_offset[start_layer],
                        [input_chan[start_layer],
                         input_dim[start_layer][0],
                         input_dim[start_layer][1]],
                        in_expand[start_layer],
                        operands[start_layer],
                        in_expand_thresh[start_layer],
                        data,
                        hw_padding[start_layer],
                        csv_file=csv,
                    )

            apb.function_footer()
            # End of input

        # ----------------------------------------------------------------------------------------

        in_map = apb.get_mem()

        if verbose:
            print('')

        def run_eltwise(
                data,
                ll,
        ):
            """
            In-flight element-wise operations
            """
            if operator[ll] == op.NONE:
                # Let element-wise do 32-bit, else 8-bit only
                o_width = output_width[ll]
            else:
                o_width = 8
            d_shape = data.shape

            data, out_size = eltwise_layer(
                eltwise[ll],
                ll,
                data[0].shape,
                output_shift[ll],
                data,
                output_width=o_width,
                operands=operands[ll],
            )
            assert out_size[0] == d_shape[1] \
                and out_size[1] == d_shape[2] and out_size[2] == d_shape[3]

            return data

        ll = start_layer
        data_buf = [data]
        # Compute layer-by-layer output and chain results into input
        while ll < layers:
            compute.debug_open(ll, base_directory, test_name, log_filename)

            # Concatenate input data if needed
            if in_sequences[ll] is not None:
                if len(in_sequences[ll]) > 1:
                    try:
                        data = np.concatenate([data_buf[i + 1] for i in in_sequences[ll]], axis=0)
                    except ValueError as err:
                        eprint('Error in input data concatenation layer:', err)
                else:
                    data = data_buf[in_sequences[ll][0] + 1]
            else:
                data = data_buf[-1]

            # Split data into multiple inputs if needed
            if operands[ll] > 1:
                if ll == start_layer and legacy_test:
                    data = np.array(np.split(data, operands[ll], axis=0))
                elif legacy_test:
                    d = np.empty((operands[ll],
                                  data.shape[0], data.shape[1], data.shape[2] // operands[ll]),
                                 dtype=np.int64)
                    for i in range(operands[ll]):
                        d[i, :, :, :] = data[:, :, i::operands[ll]]
                    data = d
                else:
                    data = np.array(np.split(data, operands[ll], axis=0))
            else:
                data = np.expand_dims(data, 0)

            in_chan = input_chan[ll]

            # Drop input channels?
            if reshape_inputs:
                if input_channel_skip[ll] > 0:
                    data = np.delete(data, np.s_[:input_channel_skip[ll]], axis=1)
                data = np.delete(data, np.s_[in_chan:], axis=1)

            show_data(
                ll,
                data.shape,
                data,
                expand=in_expand[ll],
                expand_thresh=in_expand_thresh[ll],
                operation=operator[ll],
                operands=operands[ll],
            )

            # Run in-flight element-wise operations first?
            if operands[ll] > 1 and not pool_first[ll]:
                data = np.expand_dims(run_eltwise(data, ll), 0)

            # Allow 1D <-> 2D and 2D W/L conversions
            if operator[ll] == op.CONV1D:
                assert input_dim[ll][1] == 1
                data = data.reshape(data.shape[0], -1, input_dim[ll][0])
            else:
                data = data.reshape(data.shape[0], -1, input_dim[ll][0], input_dim[ll][1])

            # In-flight pooling
            data, out_size = pooling_layer(
                ll,
                data[0].shape,
                pool[ll],
                pool_stride[ll],
                pool_average[ll],
                data,
                dilation=pool_dilation[ll],
                expand=in_expand[ll],
                expand_thresh=in_expand_thresh[ll],
                operation=operator[ll],
                operands=data.shape[0],
                rounding=avg_pool_rounding,
                debug_data=None if not log_pooling else os.path.join(base_directory, test_name),
            )

            if operator[ll] == op.CONV1D:
                if out_size[0] != in_chan \
                   or out_size[1] != pooled_dim[ll][0] or pooled_dim[ll][1] != 1:
                    eprint(f'Input dimensions do not match in layer {ll}. '
                           f'Expected: {in_chan}x{pooled_dim[ll][0]}, '
                           f'got {out_size[0]}x{out_size[1]}.')
            else:
                if out_size[0] != in_chan \
                   or out_size[1] != pooled_dim[ll][0] or out_size[2] != pooled_dim[ll][1]:
                    eprint(f'Input dimensions do not match in layer {ll}. '
                           f'Expected: {in_chan}x{pooled_dim[ll][0]}x{pooled_dim[ll][1]}, '
                           f'got {out_size[0]}x{out_size[1]}x{out_size[2]}.')

            if operands[ll] > 1 and pool_first[ll]:
                data = run_eltwise(data, ll)
            else:
                data = np.squeeze(data, axis=0)

            # Convolution or passthrough
            if operator[ll] in [op.CONV2D, op.LINEAR]:
                if flatten[ll]:
                    in_chan *= pooled_dim[ll][0] * pooled_dim[ll][1]
                    data = data.reshape(in_chan, 1, 1)
                    if verbose:
                        print_data(
                            verbose,
                            f'FLATTEN TO {in_chan}x1x1',
                            data,
                            data.shape,
                            1,
                            in_chan,
                        )

                if not bypass[ll]:
                    k = kernel[ll].reshape(
                            output_chan[ll],
                            in_chan // conv_groups[ll],
                            kernel_size[ll][0],
                            kernel_size[ll][1],
                        )
                else:
                    k = np.full(
                            (output_chan[ll], in_chan, kernel_size[ll][0], kernel_size[ll][0]),
                            1,
                            dtype=np.int64,
                        )

                out_buf, out_size = conv2d_layer(
                    ll,
                    data.shape,
                    kernel_size[ll],
                    output_shift[ll],
                    output_chan[ll],
                    padding[ll],
                    dilation[ll],
                    stride[ll],
                    activation[ll],
                    k,
                    bias[ll],
                    data,
                    output_width=output_width[ll],
                    groups=conv_groups[ll],
                    bypass=bypass[ll],
                )
            elif operator[ll] == op.CONVTRANSPOSE2D:
                if not bypass[ll]:
                    k = kernel[ll].reshape(
                            output_chan[ll],
                            in_chan // conv_groups[ll],
                            kernel_size[ll][0],
                            kernel_size[ll][1],
                        )
                else:
                    k = np.full(
                            (output_chan[ll], in_chan, kernel_size[ll][0], kernel_size[ll][0]),
                            1,
                            dtype=np.int64,
                        )

                out_buf, out_size = convtranspose2d_layer(
                    ll,
                    data.shape,
                    kernel_size[ll],
                    output_shift[ll],
                    output_chan[ll],
                    padding[ll],
                    dilation[ll],
                    stride[ll],
                    output_padding[ll],
                    activation[ll],
                    k,
                    bias[ll],
                    data,
                    output_width=output_width[ll],
                    groups=conv_groups[ll],
                    bypass=bypass[ll],
                )
            elif operator[ll] == op.CONV1D:
                if not bypass[ll]:
                    k = kernel[ll].reshape(
                            output_chan[ll],
                            input_chan[ll] // conv_groups[ll],
                            kernel_size[ll][0],
                        )
                else:
                    k = np.full(
                            (output_chan[ll], input_chan[ll], kernel_size[ll][0],),
                            1,
                            dtype=np.int64,
                        )

                out_buf, out_size = conv1d_layer(
                    ll,
                    data.shape,
                    kernel_size[ll][0],
                    output_shift[ll],
                    output_chan[ll],
                    padding[ll][0],
                    dilation[ll][0],
                    stride[ll][0],
                    activation[ll],
                    k,
                    bias[ll],
                    data,
                    output_width=output_width[ll],
                    groups=conv_groups[ll],
                    bypass=bypass[ll],
                )
            elif operator[ll] == op.NONE:  # '0'D (pooling only or passthrough)
                out_buf, out_size = passthrough_layer(
                    ll,
                    data.shape,
                    data,
                )
            else:
                eprint(f'Unknown operator `{op.string(operator[ll])}`.')

            assert out_size[0] == output_chan[ll] \
                and out_size[1] == output_dim[ll][0] and out_size[2] == output_dim[ll][1]

            # Write .mem file for output or create the C check_output() function to
            # verify the output
            out_map = [None] * tc.dev.C_GROUP_OFFS * tc.dev.P_NUMGROUPS
            if block_mode:
                if ll == terminating_layer:
                    filename = output_filename + '.mem'  # Final output
                else:
                    filename = f'{output_filename}-{ll}.mem'  # Intermediate output
                filemode = 'w'
            else:
                if ll == terminating_layer:
                    filename = c_filename + ('_riscv' if riscv else '') + '.c'  # Final output
                else:
                    filename = None  # Intermediate output - used for layer overwrite check
                filemode = 'a'

            try:
                if filename:
                    memfile = open(os.path.join(base_directory, test_name, filename),
                                   mode=filemode)
                else:
                    memfile = None
                apb.set_memfile(memfile)

                if state.generate_kat:
                    apb.output(f'// Expected output of layer {ll} for {test_name} '
                               'given the sample input (known-answer test)\n'
                               '// Delete this function for production code\n')
                    if sampleoutput_header is not None:
                        apb.output('static const uint32_t sample_output[] = SAMPLE_OUTPUT;\n')
                    apb.function_header(dest='wrapper', prefix='', function='check_output')
                    if ll == terminating_layer and mlator \
                       and not state.mlator_noverify and not embedded_code:
                        apb.verify_unload(
                            ll,
                            in_map,
                            None,
                            out_buf,
                            output_processor_map[ll],
                            out_size,
                            out_offset[ll],
                            out_expand[ll],
                            out_expand_thresh[ll],
                            output_width[ll],
                            overwrite_ok or streaming[ll],
                            mlator=False,
                            write_gap=write_gap[ll],
                            final_layer=terminating_layer,
                        )
                    if log_intermediate:
                        filename2 = f'{output_filename}-{ll}.mem'  # Intermediate output
                        memfile2 = open(os.path.join(base_directory, test_name, filename2),
                                        mode='w')
                        apb2 = apbaccess.apbwriter(
                            memfile2,
                            verify_writes=False,
                            embedded_code=False,
                            write_zero_registers=True,
                            master=groups_used[0] if oneshot > 0 or stopstart else False,
                            riscv=None,
                            fast_fifo=False,
                            input_chan=input_chan[start_layer],
                            debug_mem=True,
                            test_name=test_name,
                        )
                        out_map2 = [None] * tc.dev.C_GROUP_OFFS * tc.dev.P_NUMGROUPS
                        apb2.verify_unload(
                            ll,
                            in_map,
                            out_map2,
                            out_buf,
                            output_processor_map[ll],
                            out_size,
                            out_offset[ll],
                            out_expand[ll],
                            out_expand_thresh[ll],
                            output_width[ll],
                            overwrite_ok or streaming[ll],
                            mlator=mlator if ll == terminating_layer else False,
                            write_gap=write_gap[ll],
                        )
                    apb.verify_unload(
                        ll,
                        in_map,
                        out_map,
                        out_buf,
                        output_processor_map[ll],
                        out_size,
                        out_offset[ll],
                        out_expand[ll],
                        out_expand_thresh[ll],
                        output_width[ll],
                        overwrite_ok or (streaming[ll] if ll != start_layer
                                         else (streaming[ll] and fifo)),
                        mlator=mlator if ll == terminating_layer else False,
                        write_gap=write_gap[ll],
                        final_layer=terminating_layer,
                    )
                    if debug_snoop:
                        apb.verify_ctl(group, tc.dev.REG_SNP1_ACC, None, snoop[24],
                                       comment=' // Verify snoop 1 data accumulator')
                        apb.verify_ctl(group, tc.dev.REG_SNP1_HIT, None, snoop[25],
                                       comment=' // Verify snoop 1 match hit accumulator')
                        apb.verify_ctl(group, tc.dev.REG_SNP1_MAX, None, snoop[26],
                                       comment=' // Verify snoop 1 match max accumulator')
                        apb.verify_ctl(group, tc.dev.REG_SNP1_AM, None, snoop[27],
                                       comment=' // Verify snoop 1 match address register')
                        apb.verify_ctl(group, tc.dev.REG_SNP2_ACC, None, snoop[28],
                                       comment=' // Verify snoop 2 data accumulator')
                        apb.verify_ctl(group, tc.dev.REG_SNP2_HIT, None, snoop[29],
                                       comment=' // Verify snoop 2 match hit accumulator')
                        apb.verify_ctl(group, tc.dev.REG_SNP2_MAX, None, snoop[30],
                                       comment=' // Verify snoop 2 match max accumulator')
                        apb.verify_ctl(group, tc.dev.REG_SNP2_AM, None, snoop[31],
                                       comment=' // Verify snoop 2 match address register')

                    apb.verify_unload_finalize()
                    apb.function_footer(dest='wrapper')  # check_output()
            finally:
                if memfile:
                    memfile.close()

            if not np.any(out_buf):
                wprint(f'Layer {ll}: All output values for the given sample input are zero. '
                       'The generated known-answer test for this network may not be meaningful. '
                       'See the log file for details.')

            data_buf.append(out_buf.reshape(out_size))
            if next_sequence[ll] != -1 and streaming[next_sequence[ll]]:
                # When streaming, the output should not overwrite the input of prior layers since
                # these layers are still needed.
                in_map = [a if a is not None else b for a, b, in zip(in_map, out_map)]
            else:
                in_map = out_map

            compute.debug_close()

            if simulated_sequence[ll] is not None:
                if simulated_sequence[ll] == -1:
                    break
                ll = simulated_sequence[ll]
            else:
                if next_sequence[ll] == -1:
                    break
                ll = next_sequence[ll]

        data = data_buf[-1]

        if not block_mode:
            with open(os.path.join(base_directory, test_name, filename), mode=filemode) as memfile:
                apb.set_memfile(memfile)

                if state.softmax or embedded_code and state.unload:
                    apb.unload(
                        output_processor_map[terminating_layer],
                        out_size,
                        out_offset[terminating_layer],
                        out_expand[terminating_layer],
                        out_expand_thresh[terminating_layer],
                        output_width[terminating_layer],
                        write_gap=write_gap[terminating_layer],
                    )

                if state.softmax:
                    apb.softmax_layer(
                        output_width=output_width[terminating_layer],
                        shift=8 - abs(quantization[terminating_layer])
                        if not bypass[terminating_layer] else 0,
                    )

                summary_stats = '/*\n' + \
                                stats.summary(factor=repeat_layers, spaces=2,
                                              group_bias_max=group_bias_max) + \
                                '*/\n'
                apb.main()
                apb.output(summary_stats + '\n')

        # Close header files
        if sampledata_header is not None:
            sampledata_header.close()
        if sampleoutput_header is not None:
            sampleoutput_header.close()
        if weight_header is not None:
            weight_header.close()
        if apifile is not None:
            apifile.close()
        if state.rtl_preload or result_output:
            apb.write_mem(base_directory, test_name)

        # Create run_test.sv
        if not embedded_code and not block_mode:
            if not timeout:
                # If no timeout specified, calculate one based on reads/writes
                timeout = 10 * (apb.get_time() + rtlsim.GLOBAL_TIME_OFFSET)
                if zero_sram:
                    timeout += 16
            rtlsim.create_runtest_sv(
                test_name,
                timeout,
                riscv=riscv,
                groups_used=groups_used,
            )
            assets.copy('assets', 'rtlsim-ai' + str(device), base_directory, test_name)
            if riscv_cache:
                assets.copy('assets', 'rtlsim-riscv-cache-ai' + str(device), base_directory,
                            test_name)
            elif riscv_flash:
                assets.copy('assets', 'rtlsim-riscv-flash-ai' + str(device), base_directory,
                            test_name)
            elif riscv:
                assets.copy('assets', 'rtlsim-riscv-ai' + str(device), base_directory, test_name)
            if result_output:
                assets.copy('assets', 'rtlsim-verify-output', base_directory, test_name)
        elif block_mode:
            assets.copy('assets', 'blocklevel-ai' + str(device), base_directory, test_name)
        elif embedded_code:
            output_count = output_chan[terminating_layer] \
                * output_dim[terminating_layer][0] * output_dim[terminating_layer][1]
            insert = summary_stats + \
                '\n/* Number of outputs for this network */\n' \
                f'#define CNN_NUM_OUTPUTS {output_count}'
            if timer is not None:
                insert += '\n\n/* Use this timer to time the inference */\n' \
                          f'#define CNN_INFERENCE_TIMER MXC_TMR{timer}'

            if riscv:
                assets.from_template('assets', 'embedded-riscv-ai' + str(device), base_directory,
                                     test_name, board_name)
            else:
                assets.from_template('assets', 'embedded-ai' + str(device), base_directory,
                                     test_name, board_name)
            assets.from_template('assets', 'eclipse', base_directory, test_name, board_name)
            assets.from_template('assets', 'vscode', base_directory, test_name, board_name)
            assets.from_template('assets', 'device-all', base_directory,
                                 test_name, board_name, insert=insert)
            assets.from_template('assets', 'device-ai' + str(device), base_directory,
                                 test_name, board_name)

        print(stats.summary(factor=repeat_layers, group_bias_max=group_bias_max))

        return test_name
