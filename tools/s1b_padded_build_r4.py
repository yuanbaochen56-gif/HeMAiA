"""Cold builds: 16 legacy images, unchanged default S1b, opt-in padded S1b.

Reuse the reviewed isolated build recipe with explicit, checked substitutions.
Owned commands keep that recipe's timeout path: it signals only the process
group of the child this runner started.
"""
import hashlib
import json
from pathlib import Path

W = Path(__file__).resolve().parents[1]
E = W / "evidence/PaddedS1bR4"


def replace_once(source, old, new):
    assert source.count(old) == 1, old
    return source.replace(old, new)


def main():
    path = W / "tools/s1b_build_r1.py"
    before = json.loads((E / "before.json").read_text())
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before["sources"][str(path.relative_to(W))]
    build_dir = E / "build"
    index = 2
    while build_dir.exists():
        build_dir = E / f"build_{index}"
        index += 1
    source = path.read_text()
    source = replace_once(source, 'E = W / "evidence/BuildS1bR1"',
                          f'E = Path({str(build_dir)!r})')
    # Keep s1b_build_r1.command(): on timeout or integrity failure it signals only
    # the process group of the child this runner started (start_new_session).
    source = replace_once(source,
        'lower = E / "dependency-lower"\n            shutil.copytree(H13 / ".bender", lower, symlinks=True)',
        'lower = W / "evidence/BuildS1bR1/dependency-lower"')
    source = replace_once(source, "warning_namespace = {}", "warning_namespace = {'sha': sha}")
    source = replace_once(source, '            for index, ref in enumerate(cases):', '''            cases.append(dict(workload="dma_chain_sdc_recover_2cluster", mode="recover",
                              cfg="target/rtl/cfg/hemaia_ci.hjson", padded_dumps=True))
            prior_path = W / "evidence/BuildS1bR3/progress.json"
            prior = json.loads(prior_path.read_text())
            assert prior["status"] == "PASS" and len(prior["images"]) == 17
            state["default_reference"] = dict(path=str(prior_path), sha256=sha(prior_path))
            for index, ref in enumerate(cases):''')
    source = replace_once(source,
        'directory = E / "images" / f"{ref[\'workload\']}_{ref[\'mode\']}"',
        'directory = E / "images" / (f"{ref[\'workload\']}_{ref[\'mode\']}" + '
        '("_padded" if ref.get("padded_dumps") else ""))')
    source = replace_once(source, '                    log = directory / "build.log"',
        '''                    if ref.get("padded_dumps"):
                        cmd.append("S1B_PADDED_DUMPS=1")
                    log = directory / "build.log"''')
    source = replace_once(source, '                state["images"].append(actual)', '''                actual["padded_dumps"] = bool(ref.get("padded_dumps"))
                baseline, = [r for r in prior["images"]
                             if (r["workload"], r["mode"]) == (actual["workload"], mode)]
                reference_output = Path(baseline["output"])
                reference_task, = reference_output.glob("task_*")
                for part, hashes in actual["part_hashes"].items():
                    if actual["padded_dumps"] and part != "mempool":
                        continue
                    assert hashes == baseline["part_hashes"][part], (actual["workload"], part)
                    for filename in hashes:
                        assert (task_dir / "bin" / part / filename).read_bytes() == (
                            reference_task / "bin" / part / filename).read_bytes(), filename
                generated_old = reference_output / "generated"
                assert products(generated).keys() == products(generated_old).keys()
                for filename in products(generated):
                    old_bytes = (generated_old / filename).read_bytes()
                    if actual["padded_dumps"] and filename == "offload_bingo_hw.h":
                        assert old_bytes.count(b"char hex[129] = {0};") == 10
                        old_bytes = old_bytes.replace(b"char hex[129] = {0};",
                            b"char hex[136] __attribute__((aligned(8))) = {0};")
                    assert (generated / filename).read_bytes() == old_bytes, filename
                if not actual["padded_dumps"]:
                    assert (output / "test_cfg_unpatched.bin").read_bytes() == (
                        reference_output / "test_cfg_unpatched.bin").read_bytes()
                    # GCC embeds a fresh ccXXXXXX.o name. BuildS1bR2 and BuildS1bR3
                    # already differ by only those six characters; loaded banks do not.
                    import re
                    new_elf = (output / "test_cfg_host.elf").read_bytes()
                    old_elf = (reference_output / "test_cfg_host.elf").read_bytes()
                    temp_name = re.compile(rb"cc[A-Za-z0-9]+\.o")
                    new_names, old_names = temp_name.findall(new_elf), temp_name.findall(old_elf)
                    assert len(new_names) == len(old_names) == 1, (new_names, old_names)
                    assert temp_name.sub(b"ccTEMP.o", new_elf) == temp_name.sub(b"ccTEMP.o", old_elf), \
                        "host ELF differs outside the compiler temporary object name"
                    actual["host_elf_delta"] = ("identical" if new_elf == old_elf
                                                else "compiler-temporary-object-name-only")
                    assert actual["image_id"] == baseline["image_id"]
                    actual["default_byte_identical"] = True
                else:
                    assert actual["image_id"] != baseline["image_id"]
                    actual["device_byte_identical"] = True
                    actual["only_generated_delta"] = "Ten aligned zeroed hex[136] declarations"
                state["images"].append(actual)''')
    source = replace_once(source, '            assert len(state["images"]) == 17', '''            assert len(state["images"]) == 18
            state["exact_comparison"] = dict(legacy_images=16, default_S1b_image=True,
                loaded_image_byte_identical=True, recovery_device_image=True,
                byte_comparison=True, hashes_also_checked=True,
                host_elf_delta="compiler-temporary-object-name-only",
                default_generated_artifacts_identical=True,
                padded_only_generated_delta="Ten aligned zeroed hex[136] declarations")''')
    # Retain a concrete inspectable executed recipe, including all unchanged gates.
    recipe = E / (build_dir.name + "_recipe.py")
    recipe.write_text(source)
    namespace = {"__file__": str(Path(__file__)), "__name__": "s1b_padded_build_recipe"}
    exec(compile(source, str(recipe), "exec"), namespace)
    return namespace["main"]()


if __name__ == "__main__":
    raise SystemExit(main())
