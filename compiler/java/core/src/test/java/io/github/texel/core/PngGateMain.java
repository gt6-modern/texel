package io.github.texel.core;

import java.io.IOException;
import java.nio.charset.Charset;
import java.nio.file.FileVisitResult;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.SimpleFileVisitor;
import java.nio.file.attribute.BasicFileAttributes;
import java.util.ArrayList;
import java.util.List;

/**
 * **永久双路径 PNG 门禁**（PRD §18.4）：同一个 PNG 接受集与拒绝集，既要在 Importer 的 decoder 上跑，
 * 也要在每个 compiler 的 decoder 上跑，接受例解出的 RGBA8 必须一致。
 *
 * <p>本文件负责 compiler 这一侧：
 * <ul>
 *   <li>接受集：v0 语料里每个带 {@code input.png} 的用例，用 Java decoder 解码后与语料里由
 *       **Importer 一侧**算出的 {@code expected.pixels.sha256} 比对——跨实现像素一致的直接判据；</li>
 *   <li>拒绝集：{@code negative/neg-png-*}（以及实测 CRC 损坏的真实素材用例）必须一律 {@code PXL006}。</li>
 * </ul>
 *
 * <p>它只读语料、不写任何文件，也不依赖 gradle 之外的东西。
 */
public final class PngGateMain {

    private static final Charset UTF8 = Charset.forName("UTF-8");
    private static final List<String> FAILURES = new ArrayList<String>();

    private PngGateMain() {
    }

    public static void main(String[] args) throws IOException {
        Path repo = Paths.get(System.getProperty("txel.repo", ".")).toAbsolutePath().normalize();
        Path casesDir = repo.resolve("spec").resolve("conformance").resolve("cases");
        final int[] counts = new int[3];
        Files.walkFileTree(casesDir, new SimpleFileVisitor<Path>() {
            @Override
            public FileVisitResult visitFile(Path file, BasicFileAttributes attributes)
                    throws IOException {
                if (!"input.png".equals(file.getFileName().toString())) {
                    return FileVisitResult.CONTINUE;
                }
                Path directory = file.getParent();
                String casePath = casesDir.relativize(directory).toString().replace('\\', '/');
                Path expected = directory.resolve("expected.pixels.sha256");
                if (!Files.isRegularFile(expected)) {
                    return FileVisitResult.CONTINUE;
                }
                counts[0]++;
                acceptCase(casePath, file, expected);
                return FileVisitResult.CONTINUE;
            }
        });
        Path negatives = casesDir.resolve("negative");
        java.nio.file.DirectoryStream<Path> stream = Files.newDirectoryStream(negatives);
        List<String> names = new ArrayList<String>();
        try {
            for (Path item : stream) {
                String name = item.getFileName().toString();
                // 按**目录结构**划集合：负例目录里有 input.png，且 expected.error 是 PXL006。
                // 以前用名字前缀 `neg-png-`，改个名就静默退出集合，命名约定一漂移判据就消失，
                // 而且会与 Python 侧的结构化判定不一致（2026-11-14 审查 P2）。
                Path input = item.resolve("input.png");
                Path error = item.resolve("expected.error");
                if (!Files.isRegularFile(input) || !Files.isRegularFile(error)) {
                    continue;
                }
                if (!"PXL006".equals(new String(Files.readAllBytes(error), UTF8).trim())) {
                    continue;
                }
                names.add(name);
            }
        } finally {
            stream.close();
        }
        java.util.Collections.sort(names);
        for (String name : names) {
            Path input = negatives.resolve(name).resolve("input.png");
            if (!Files.isRegularFile(input)) {
                FAILURES.add(name + "：拒绝集用例缺少 input.png");
                continue;
            }
            counts[1]++;
            rejectCase(name, input);
        }
        System.out.println("PNGGATE= accept " + counts[0] + " reject " + counts[1]
                + " FAILURES=" + FAILURES.size());
        for (String failure : FAILURES) {
            System.out.println("  FAIL " + failure);
        }
        if (!FAILURES.isEmpty()) {
            System.out.println("PNG GATE FAILED");
            System.exit(1);
        }
        System.out.println("PNG GATE OK");
    }

    private static void acceptCase(String casePath, Path input, Path expected) {
        try {
            byte[] png = Files.readAllBytes(input);
            PngReader.Decoded decoded = PngReader.decode(png);
            String want = Files.readAllLines(expected, UTF8).get(0).trim();
            String got = PixelDigest.sha256Hex(decoded.width, decoded.height, decoded.rgba);
            if (!want.equals(got)) {
                FAILURES.add(casePath + "：Java decoder 的像素摘要与 Importer 一侧不一致");
            }
        } catch (PxlFormatException failure) {
            FAILURES.add(casePath + "：接受例却被拒绝 " + failure.code() + " " + failure.getMessage());
        } catch (IOException failure) {
            FAILURES.add(casePath + "：读文件失败 " + failure);
        }
    }

    private static void rejectCase(String casePath, Path input) {
        try {
            PngReader.decode(Files.readAllBytes(input));
            FAILURES.add(casePath + "：应当被拒绝却解码成功");
        } catch (PxlFormatException failure) {
            if (!Codes.PXL006.equals(failure.code())) {
                FAILURES.add(casePath + "：拒绝码应为 PXL006，实际 " + failure.code());
            }
            // Core §13.1 / §13.2c：有 expected.error_detail 时，失败消息必须命中其中至少一行。
            // 只判码时"码对了但错在别的分支"照样通过（签名错被 CRC 分支拒掉也算过）。
            Path detail = input.getParent().resolve("expected.error_detail");
            if (Files.isRegularFile(detail)) {
                boolean matched = false;
                try {
                    for (String line : Files.readAllLines(detail, UTF8)) {
                        if (!line.isEmpty() && failure.getMessage().contains(line)) {
                            matched = true;
                            break;
                        }
                    }
                } catch (IOException broken) {
                    FAILURES.add(casePath + "：读 expected.error_detail 失败 " + broken);
                    return;
                }
                if (!matched) {
                    FAILURES.add(casePath + "：失败消息没有命中 expected.error_detail 的任何一行（实际 "
                            + failure.getMessage() + "）");
                }
            }
        } catch (IOException failure) {
            FAILURES.add(casePath + "：读文件失败 " + failure);
        }
    }
}
