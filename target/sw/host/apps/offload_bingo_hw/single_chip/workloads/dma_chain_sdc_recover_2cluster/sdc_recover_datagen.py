"""Fixed stable input and independently calculated final copy oracle."""
def emit_header_file():
    data = [((i * 37 + 11) ^ (i >> 2)) & 255 for i in range(64)]
    golden = [((37 * i + 11) % 256) ^ (i // 4) for i in range(64)]
    return ("#pragma once\n#include <stdint.h>\n"
            "#if !defined(BINGO_SDC_RECOVER) || !defined(BINGO_SDC) || "
            "!defined(BINGO_SDC_QUIET) || !BINGO_TEST_CFG\n"
            '#error "Recovery workload requires RECOVER, SDC, QUIET and TEST_CFG"\n'
            "#endif\nuint8_t s1b_input[64] __attribute__((aligned(8))) = {" +
            ",".join(map(str,data)) + "};\n"
            "const uint8_t s1b_golden[64] __attribute__((aligned(8))) = {" +
            ",".join(map(str,golden)) + "};\n")
