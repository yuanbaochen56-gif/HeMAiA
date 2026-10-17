// Copyright 2026 KU Leuven.
// Solderpad Hardware License, Version 0.51, see LICENSE for details.
// SPDX-License-Identifier: SHL-0.51

// Testbench of the two division register sets of hemaia_clk_rst_controller
// (hw/vendor/kuleuven_micas_hemaia_clk_rst_controller), with two masters that
// program different clocks at the same time, as in HeMAiA: the host
// (domains 0 and 3, hemaia_clk_rst_controller.h enable_clk_domain: read the
// packed register, change one byte, write it back, set the valid bit) and the
// bingo power manager (domains 1 and 2, random levels). Two controllers run
// the same random operations with the same timing:
//   0  the PM writes the packed registers as bingo_hw_manager_pm does with
//      DivisionPerDomain = 0 (the 8-byte word of domains 0-7: its level, its
//      earlier levels for 1 and 2, zeros elsewhere), then the valid register;
//   1  the PM writes its domain's own register (0x38 + 8d, then nothing at
//      0x3c + 8d) and loads it with DOMAIN_DIVISION_VALID (0x34)
//      (DivisionPerDomain = 1).
// The 64-bit PM writes reach the controller as two 32-bit writes, as through
// the converter of HeMAiA. Control and master clock 500 MHz, as in the
// testharness. Checks:
//   K1 every load of a divider (its new_divisor_ready at count 0) takes the
//      value of the last valid write of that domain, as its writer meant it;
//      with 0 the two masters spoil each other's loads (the test needs both
//      kinds at least once), with 1 never;
//   K2 with +NO_HOST (the PM alone): nothing is spoiled, and the loads happen
//      at the same times with both controllers (the PM's bus sequence is the
//      same); with the host the clocks of controller 0 diverge after the first
//      spoiled load, so K2 is only checked without it;
//   K3 at the end every clock runs at the period of its last load;
//   K4 no write is answered with an error (the hole at 0x3c + 8d included),
//      and the registers read back what was written.
// Files (vlog -sv, +incdir to the include dirs of axi, common_cells and
// register_interface): axi_pkg, cf_math_pkg, fifo_v3, the register_interface
// sources reg_intf, prim_subreg_arb, prim_subreg_ext, prim_subreg,
// axi_lite_to_reg, the tc_clk cells of tech_cells_generic, then the vendored
// regs/*_reg_pkg.sv, regs/*_reg_top.sv, hemaia_pll_wrapper_rtl.sv,
// hemaia_clock_divider.sv, hemaia_reset_controller.sv,
// hemaia_clk_rst_controller.sv, and this file.

`timescale 1ns/1ps
`include "axi/typedef.svh"

module tb_hemaia_clk_rst_division;
  typedef logic [47:0] addr_t;
  typedef logic [31:0] data_t;
  typedef logic [3:0]  strb_t;
  `AXI_LITE_TYPEDEF_ALL(lite, addr_t, data_t, strb_t)

  localparam int unsigned NumClocks = 4;
  localparam int unsigned Ops = 1500;           // operations of each master
  localparam int unsigned Default[NumClocks] = '{8, 6, 6, 8};

  logic clk = 1'b0, rst_n = 1'b0;
  always #1ns clk = ~clk;

  // The random operations, the same for both controllers
  int unsigned pm_domain[Ops], pm_gap[Ops], pm_valid_gap[Ops];
  logic [7:0]  pm_level[Ops];
  int unsigned host_domain[Ops], host_gap[Ops], host_write_gap[Ops], host_valid_gap[Ops];
  logic [7:0]  host_value[Ops];
  logic        ops_ready = 1'b0;
  int unsigned done[2][2];   // [controller][0 PM, 1 host]
  int unsigned errors = 0;

  initial begin
    automatic logic [7:0] pm_levels[5] = '{8'd2, 8'd3, 8'd6, 8'd12, 8'd25};
    for (int unsigned i = 0; i < Ops; i++) begin
      pm_domain[i]      = 1 + ($urandom % 2);
      pm_level[i]       = pm_levels[$urandom % 5];
      pm_gap[i]         = $urandom % 12;
      pm_valid_gap[i]   = $urandom % 3;
      host_domain[i]    = ($urandom % 2) ? 3 : 0;
      host_value[i]     = 8'(2 + ($urandom % 19));
      host_gap[i]       = $urandom % 12;
      host_write_gap[i] = $urandom % 4;
      host_valid_gap[i] = $urandom % 3;
    end
    ops_ready = 1'b1;
  end

  for (genvar m = 0; m < 2; m++) begin : gen_ctrl
    lite_req_t            req;
    lite_resp_t           rsp;
    logic [NumClocks-1:0] clk_o, rst_no;

    hemaia_clk_rst_controller #(
      .USE_VENDOR_PLL   ( 0           ),
      .NumClocks        ( NumClocks   ),
      .MaxDivisionWidth ( 8           ),
      .DefaultDivision  ( '{8, 6, 6, 8} ),
      .ResetDelays      ( '{default: 1} ),
      .axi_lite_req_t   ( lite_req_t  ),
      .axi_lite_rsp_t   ( lite_resp_t )
    ) i_dut (
      .test_mode_i        ( 1'b0   ),
      .control_clk_i      ( clk    ),
      .control_rst_ni     ( rst_n  ),
      .axi_lite_req_i     ( req    ),
      .axi_lite_rsp_o     ( rsp    ),
      .mst_clk_i          ( clk    ),
      .mst_rst_ni         ( rst_n  ),
      .clk_obs_o          (        ),
      .clk_o              ( clk_o  ),
      .rst_no             ( rst_no ),
      .pll_bypass_i       ( 1'b0   ),
      .pll_en_i           ( 1'b0   ),
      .pll_post_div_sel_i ( '0     ),
      .pll_lock_o         (        )
    );

    // One 32-bit access at a time; the two masters take turns (a 64-bit PM write
    // keeps the bus for both of its 32-bit halves, as the converter does)
    semaphore bus = new(1);
    initial begin
      req = '0;
    end

    task automatic write32(input addr_t addr, input data_t data);
      @(posedge clk);
      #0.1ns;
      req.aw.addr  = addr;
      req.aw_valid = 1'b1;
      req.w.data   = data;
      req.w.strb   = 4'hf;
      req.w_valid  = 1'b1;
      req.b_ready  = 1'b1;
      fork
        begin
          do @(posedge clk); while (!rsp.aw_ready);
          #0.1ns req.aw_valid = 1'b0;
        end
        begin
          do @(posedge clk); while (!rsp.w_ready);
          #0.1ns req.w_valid = 1'b0;
        end
      join
      while (!rsp.b_valid) @(posedge clk);
      if (rsp.b.resp != axi_pkg::RESP_OKAY) begin
        $error("[CLKDIV] controller %0d: write %h at %h answered %0d", m, data, addr, rsp.b.resp);
        errors++;
      end
      @(posedge clk);
      #0.1ns req.b_ready = 1'b0;
    endtask

    task automatic read32(input addr_t addr, output data_t data);
      @(posedge clk);
      #0.1ns;
      req.ar.addr  = addr;
      req.ar_valid = 1'b1;
      req.r_ready  = 1'b1;
      do @(posedge clk); while (!rsp.ar_ready);
      #0.1ns req.ar_valid = 1'b0;
      while (!rsp.r_valid) @(posedge clk);
      data = rsp.r.data;
      if (rsp.r.resp != axi_pkg::RESP_OKAY) begin
        $error("[CLKDIV] controller %0d: read at %h answered %0d", m, addr, rsp.r.resp);
        errors++;
      end
      @(posedge clk);
      #0.1ns req.r_ready = 1'b0;
    endtask

    // What the writer of the next valid write of each domain means it to load,
    // and what the last valid write of each domain meant (taken when the
    // controller sees the write)
    logic [NumClocks-1:0][7:0] pending, intended;
    logic [7:0] pm_shadow[NumClocks];   // the levels the packed PM applied
    int unsigned spoiled_pm = 0, spoiled_host = 0, loads_pm = 0, loads_host = 0;
    realtime     load_times[NumClocks][$];
    logic [7:0]  last_load[NumClocks];

    logic [7:0] last_level[NumClocks], last_value[NumClocks];   // what each master wrote last

    // PM
    initial begin
      for (int d = 0; d < NumClocks; d++) begin
        pm_shadow[d] = '0;
        last_load[d] = 8'(Default[d]);
        intended[d]  = 8'(Default[d]);
      end
      done[m][0] = 0;
      wait (ops_ready && rst_n);
      repeat (5) @(posedge clk);
      for (int unsigned i = 0; i < Ops; i++) begin
        automatic int unsigned d = pm_domain[i];
        repeat (pm_gap[i]) @(posedge clk);
        bus.get(1);
        pending[d] = pm_level[i];
        if (m == 0) begin
          // The 8-byte word of domains 0-7: the new level, the earlier levels of
          // the PM's domains, zeros (levels it never set) for the others
          automatic logic [63:0] word = '0;
          pm_shadow[d] = pm_level[i];
          for (int k = 1; k <= 2; k++) word[8 * k +: 8] = pm_shadow[k];
          write32(48'h08, word[31:0]);
          write32(48'h0c, word[63:32]);
        end else begin
          write32(48'h38 + 8 * d, 32'(pm_level[i]));
          write32(48'h3c + 8 * d, '0);
        end
        bus.put(1);
        repeat (pm_valid_gap[i]) @(posedge clk);
        bus.get(1);
        write32(m == 0 ? 48'h04 : 48'h34, 32'd1 << d);
        bus.put(1);
        last_level[d] = pm_level[i];
      end
      done[m][0] = 1;
    end

    // Host: enable_clk_domain(d, value)
    initial begin
      automatic data_t word;
      done[m][1] = 0;
      if ($test$plusargs("NO_HOST")) begin
        last_value[0] = '0;
        last_value[3] = '0;
        done[m][1] = 1;
        wait (0);
      end
      wait (ops_ready && rst_n);
      repeat (5) @(posedge clk);
      for (int unsigned i = 0; i < Ops; i++) begin
        automatic int unsigned d = host_domain[i];
        repeat (host_gap[i]) @(posedge clk);
        bus.get(1);
        read32(48'h08, word);
        bus.put(1);
        repeat (host_write_gap[i]) @(posedge clk);
        word[8 * d +: 8] = host_value[i];
        bus.get(1);
        write32(48'h08, word);
        bus.put(1);
        repeat (host_valid_gap[i]) @(posedge clk);
        bus.get(1);
        pending[d] = host_value[i];
        write32(48'h04, 32'd1 << d);
        bus.put(1);
        last_value[d] = host_value[i];
      end
      done[m][1] = 1;
    end

    // The controller sees a valid write: what its writer meant becomes the
    // value of the next load. A load (K1): the divider takes its divisor.
    for (genvar d = 0; d < NumClocks; d++) begin : gen_monitor
      always @(posedge clk) begin
        if (rst_n) begin
          if (i_dut.valid_set[d] || i_dut.domain_valid_set[d]) intended[d] <= pending[d];
          if (i_dut.gen_clock_divider[d].i_clk_divider.new_divisor_ready &&
              i_dut.gen_clock_divider[d].i_clk_divider.cnt == 0) begin
            automatic logic [7:0] value = i_dut.gen_clock_divider[d].i_clk_divider.divisor_i;
            load_times[d].push_back($realtime);
            last_load[d] = value;
            if (d inside {1, 2}) begin
              loads_pm++;
              if (value != intended[d]) spoiled_pm++;
            end else begin
              loads_host++;
              if (value != intended[d]) spoiled_host++;
            end
          end
        end
      end
    end
  end

  // K3: the period of a clock over four cycles (0: no edge within 400 ns)
  task automatic period(input int m, input int d, output realtime p);
    realtime t0 = 0;
    p = 0;
    fork
      begin
        if (m == 0) begin
          @(posedge gen_ctrl[0].clk_o[d]); t0 = $realtime;
          repeat (4) @(posedge gen_ctrl[0].clk_o[d]);
        end else begin
          @(posedge gen_ctrl[1].clk_o[d]); t0 = $realtime;
          repeat (4) @(posedge gen_ctrl[1].clk_o[d]);
        end
        p = ($realtime - t0) / 4;
      end
      #400ns;
    join_any
    disable fork;
  endtask

  initial begin
    automatic realtime p;
    automatic data_t rd;
    automatic bit no_host = $test$plusargs("NO_HOST");
    repeat (4) @(posedge clk);
    rst_n = 1'b1;
    wait (done[0][0] && done[0][1] && done[1][0] && done[1][1]);
    repeat (200) @(posedge clk);
    $display("[CLKDIV] packed PM:     %0d PM loads (%0d spoiled), %0d host loads (%0d spoiled)",
             gen_ctrl[0].loads_pm, gen_ctrl[0].spoiled_pm, gen_ctrl[0].loads_host, gen_ctrl[0].spoiled_host);
    $display("[CLKDIV] per-domain PM: %0d PM loads (%0d spoiled), %0d host loads (%0d spoiled)",
             gen_ctrl[1].loads_pm, gen_ctrl[1].spoiled_pm, gen_ctrl[1].loads_host, gen_ctrl[1].spoiled_host);
    // K1
    if (no_host) begin
      if (gen_ctrl[0].spoiled_pm != 0 || gen_ctrl[0].loads_host != 0) begin
        $error("[CLKDIV] the packed PM alone: %0d loads spoiled", gen_ctrl[0].spoiled_pm);
        errors++;
      end
    end else if (gen_ctrl[0].spoiled_pm == 0 || gen_ctrl[0].spoiled_host == 0) begin
      $error("[CLKDIV] the packed PM spoiled no load: the stimulus misses the race");
      errors++;
    end
    if (gen_ctrl[1].spoiled_pm != 0 || gen_ctrl[1].spoiled_host != 0) begin
      $error("[CLKDIV] per-domain registers: %0d PM loads and %0d host loads spoiled",
             gen_ctrl[1].spoiled_pm, gen_ctrl[1].spoiled_host);
      errors++;
    end
    if (gen_ctrl[1].loads_pm < Ops / 4 || (!no_host && gen_ctrl[1].loads_host < Ops / 4)) begin
      $error("[CLKDIV] too few loads");
      errors++;
    end
    // K2
    for (int d = 0; d < NumClocks && no_host; d++) begin
      if (gen_ctrl[0].load_times[d] != gen_ctrl[1].load_times[d]) begin
        $error("[CLKDIV] domain %0d: the loads happen at other times (%0d / %0d loads)", d,
               gen_ctrl[0].load_times[d].size(), gen_ctrl[1].load_times[d].size());
        errors++;
      end
    end
    // K3
    for (int m = 0; m < 2; m++) begin
      for (int d = 0; d < NumClocks; d++) begin
        automatic realtime expected = 2.0ns * ((m == 0) ? gen_ctrl[0].last_load[d] : gen_ctrl[1].last_load[d]);
        period(m, d, p);
        if (p != expected) begin
          $error("[CLKDIV] controller %0d domain %0d: period %0t, last load %0t", m, d, p, expected);
          errors++;
        end
      end
    end
    // K4: the registers of controller 1 read back what was written last; the
    // PM never wrote the packed register there
    for (int d = 1; d <= 2; d++) begin
      gen_ctrl[1].read32(48'h38 + 8 * d, rd);
      if (rd != 32'(gen_ctrl[1].last_level[d])) begin
        $error("[CLKDIV] DOMAIN_DIVISION_C%0d reads %h, written %h", d, rd, gen_ctrl[1].last_level[d]);
        errors++;
      end
    end
    gen_ctrl[1].read32(48'h08, rd);
    if (rd != {gen_ctrl[1].last_value[3], 16'h0, gen_ctrl[1].last_value[0]}) begin
      $error("[CLKDIV] CLOCK_DIVISION_REGISTER_C0_C3 reads %h", rd);
      errors++;
    end
    if (errors == 0) $display("[CLKDIV] clock division test passed");
    else $error("[CLKDIV] %0d errors", errors);
    $finish;
  end
endmodule
