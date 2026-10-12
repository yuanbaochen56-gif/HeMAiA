// Copyright 2026 KU Leuven.
// Solderpad Hardware License, Version 0.51, see LICENSE for details.
// SPDX-License-Identifier: SHL-0.51

// Simulation-only check for clock domain crossings that may be reset while
// the system runs: on every channel, side B never pops more items than side A
// pushed since either side last entered reset. A pop without a push is a
// spurious transaction, the failure of a CDC reset on one side only.
// Bound to cdc_fifo_gray and axi_cdc when the accelerator reset is generated.

module hemaia_cdc_check #(
  parameter int unsigned NumCh = 1
) (
  input logic             clk_a_i,
  input logic             rst_a_ni,
  input logic [NumCh-1:0] push_a_i,
  input logic             clk_b_i,
  input logic             rst_b_ni,
  input logic [NumCh-1:0] pop_b_i
);
  // pragma translate_off
  `ifndef SYNTHESIS
  longint unsigned pushes [NumCh];
  longint unsigned pops   [NumCh];
  longint unsigned resets;

  initial begin
    resets = 0;
    foreach (pushes[i]) begin
      pushes[i] = 0;
      pops[i] = 0;
    end
  end

  always @(negedge rst_a_ni or negedge rst_b_ni) begin
    if ($time > 0 && pushes.sum() > 0)
      $display("[B2_CDC] %0t %m reset after %0d pushes, %0d pops", $time, pushes.sum(), pops.sum());
    resets++;
    foreach (pushes[i]) begin
      pushes[i] = 0;
      pops[i] = 0;
    end
  end

  always @(posedge clk_a_i) begin
    if (rst_a_ni) begin
      foreach (pushes[i]) if (push_a_i[i] === 1'b1) pushes[i]++;
    end
  end

  always @(posedge clk_b_i) begin
    if (rst_b_ni) begin
      foreach (pops[i]) begin
        if (pop_b_i[i] === 1'b1) begin
          if (pops[i] >= pushes[i])
            $error("[B2_CDC] %m channel %0d: pop %0d without a push (pushes %0d, resets %0d)",
                   i, pops[i] + 1, pushes[i], resets);
          pops[i]++;
        end
      end
    end
  end
  `endif
  // pragma translate_on
endmodule
