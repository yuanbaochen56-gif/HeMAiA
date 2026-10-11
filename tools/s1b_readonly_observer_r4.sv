// Input-only observation of the probed 12-bit task-ID, 62-bit internal descriptor ABI.
module s1b_readonly_observer_r4 #(
  parameter int C = 3, CL = 2, T = 4, IDW = 12, DW = 62
) (
  input logic clk_i, rst_ni,
  input logic [31:0] start_i,
  input logic [C-1:0][DW-1:0] waiting_desc,
  input logic [C-1:0][CL-1:0][DW-1:0] checkout_desc,
  input logic [C-1:0][CL-1:0][31:0] ready_desc, done_desc,
  input logic [C-1:0] waiting_pop, cond_skip,
  input logic [C-1:0][CL-1:0] checkout_pop, ready_pop, done_push,
  input logic [CL-1:0][C-1:0] check_valid, check_result, set_valid,
  input logic [CL-1:0][C-1:0][C-1:0] check_code, set_code,
  input logic [CL-1:0][C-1:0][T-1:0] check_tag, set_tag
);
  integer output_file;
  string output_path;
  logic [31:0] previous_start = 0;
  logic compare0_completed = 0;
  logic init_completed = 0;
  initial begin
    assert(C == 3 && CL == 2 && T == 4 && IDW == 12 && DW == 62)
      else $fatal(1, "Unexpected passive observer ABI");
    if (!$value$plusargs("S1B_OBSERVATIONS=%s", output_path))
      $fatal(1, "Missing runner-local S1B observations path");
    output_file = $fopen(output_path, "w");
    if (!output_file) $fatal(1, "Cannot open runner-local observations");
    $fdisplay(output_file, "SCHEMA 2 C=%0d CL=%0d TAG=%0d IDW=12 DW=62", C, CL, T);
  end
  always @(posedge clk_i) begin
    previous_start <= start_i;
    if (rst_ni) begin
      if (start_i && !previous_start) $fdisplay(output_file, "%0t START", $time);
      for (int c = 0; c < C; ++c) begin
        if (waiting_pop[c])
          $fdisplay(output_file, "%0t WAIT_RETIRE %0d %0d %0d %0d", $time,
                    waiting_desc[c][IDW+8:9], c, waiting_desc[c][IDW+17], cond_skip[c]);
        for (int cl = 0; cl < CL; ++cl) begin
          if (check_valid[cl][c] && check_result[cl][c])
            $fdisplay(output_file, "%0t CHECK %0d %0d %0d %0d %0d", $time,
                      waiting_desc[c][IDW+8:9], cl, c, check_code[cl][c], check_tag[cl][c]);
          if (set_valid[cl][c])
            $fdisplay(output_file, "%0t SET %0d %0d %0d %0d", $time,
                      cl, c, set_code[cl][c], set_tag[cl][c]);
          if (checkout_pop[c][cl])
            $fdisplay(output_file, "%0t CHECKOUT_RETIRE %0d %0d %0d %0d", $time,
                      checkout_desc[c][cl][IDW+8:9], cl, c, checkout_desc[c][cl][8:7]);
          if (ready_pop[c][cl])
            $fdisplay(output_file, "%0t DISPATCH %0d %0d %0d", $time,
                      ready_desc[c][cl][IDW-1:0], cl, c);
          if (done_push[c][cl]) begin
            $fdisplay(output_file, "%0t DONE %0d %0d %0d", $time,
                      done_desc[c][cl][IDW-1:0], cl, c);
            if (c == 1 && cl == 1 && done_desc[c][cl][IDW-1:0] == 3)
              compare0_completed <= 1;
            if (c == 2 && cl == 0 && done_desc[c][cl][IDW-1:0] == 0)
              init_completed <= 1;
          end
        end
      end
    end
  end
  final $fclose(output_file);
endmodule

module s1b_readonly_bind_r4;
bind bingo_hw_manager_top s1b_readonly_observer_r4 #(
  .C(NUM_CORES_PER_CLUSTER), .CL(NUM_CLUSTERS_PER_CHIPLET), .T(DepTagWidth),
  .IDW(TaskIdWidth), .DW(WdTaskDescWidth)
) i_s1b_readonly_observer (
  .clk_i(clk_i), .rst_ni(rst_ni), .start_i(bingo_hw_manager_start_i),
  .waiting_desc(waiting_dep_check_task_desc), .checkout_desc(checkout_queue_data_out),
  .ready_desc(ready_queue_data_out), .done_desc(done_q_data_in),
  .waiting_pop(waiting_dep_check_queue_pop), .cond_skip(cond_exec_skip),
  .checkout_pop(checkout_queue_pop), .ready_pop(ready_queue_pop), .done_push(done_q_push),
  .check_valid(dep_check_valid), .check_result(dep_check_result),
  .set_valid(dep_set_valid), .check_code(dep_check_code), .set_code(dep_set_code),
  .check_tag(dep_check_tag), .set_tag(dep_set_tag)
);
endmodule
