package io.github.texel.core;

import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.file.NoSuchFileException;
import java.nio.file.NotDirectoryException;
import java.nio.file.DirectoryStream;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

/**
 * 标准文件系统 resolver（规范 v1 §8.4）：把逻辑路径解析成 asset root 下的真实文件。
 *
 * <p>它是**安全边界**，不是路径拼接工具。四条硬要求逐条实现：
 * <ol>
 *   <li>逐段校验实际拼写：在大小写不敏感的文件系统上，{@code Parts/Blade.pxl} 不得命中
 *       {@code parts/blade.pxl}（用目录列举 + 精确比较，不靠 {@code File.exists}）；</li>
 *   <li>真实路径必须仍在 root 下，且用**路径段关系**判断（{@code Path.startsWith} 是段级比较，
 *       不是字符串前缀——{@code /a/bc} 不会被当成 {@code /a/b} 的子路径）；</li>
 *   <li>拒绝经 symlink / junction 逃出 root 的目标（对真实路径再判一次）；</li>
 *   <li>不搜索备用目录、不读环境变量、不联网。</li>
 * </ol>
 *
 * <p>缺失与拼写不符必须**分开**报告：前者 {@code PXL009}，后者 {@code PXL008}。
 */
public final class FileAssetResolver implements AssetResolver {

    private final Path root;

    private FileAssetResolver(Path root) {
        this.root = root;
    }

    /**
     * 以 {@code root} 为 asset root 构造 resolver。
     *
     * @throws IOException root 不存在或不是目录
     */
    public static FileAssetResolver rootedAt(Path root) throws IOException {
        if (root == null) {
            throw new NullPointerException("root");
        }
        Path absolute = root.toAbsolutePath().normalize();
        if (!Files.isDirectory(absolute)) {
            throw new IOException("asset root 不是目录: " + absolute);
        }
        return new FileAssetResolver(absolute.toRealPath());
    }

    /** asset root 的真实路径（已展开重解析点）。 */
    public Path root() {
        return root;
    }

    @Override
    public Result resolve(String logicalPath) {
        return resolve(logicalPath, null);
    }

    @Override
    public Result resolve(String logicalPath, String expectedKind) {
        if (logicalPath == null || logicalPath.isEmpty()) {
            throw new NullPointerException("logicalPath");
        }
        String[] segments = logicalPath.split("/");
        Path current = root;
        for (int index = 0; index < segments.length; index++) {
            String segment = segments[index];
            if (segment.isEmpty() || ".".equals(segment) || "..".equals(segment)) {
                return Result.of(Status.ESCAPE);
            }
            Path next = current.resolve(segment);
            // (1) 逐段校验实际拼写：目录里必须有**逐字节相同**的这个名字。
            if (!hasExactChild(current, segment)) {
                if (hasCaseInsensitiveChild(current, segment)) {
                    return Result.of(Status.CASE_MISMATCH);
                }
                return Result.of(Status.MISSING);
            }
            // 越界必须在**枚举下一段之前**判掉：否则外部目录的存在性会泄露成 PXL009
            // （目标存在报 PXL008、不存在报 PXL009；发布态复核 A2）。
            if (escapesRoot(next)) {
                return Result.of(Status.ESCAPE);
            }
            current = next;
        }
        // 跟随最后一级链接，但不能用会把权限错误吞成 false 的 exists/isRegularFile。
        // 真正缺失与宿主属性读取失败必须保留不同的错误类别（§8.4）。
        try {
            if (!Files.readAttributes(current, java.nio.file.attribute.BasicFileAttributes.class)
                    .isRegularFile()) {
                return Result.of(Status.MISSING);
            }
            // (2)(3) 真实路径必须仍在 root 之下（段级比较 + 解析重解析点后再判一次）。
            Path real = current.toRealPath();
            if (!real.startsWith(root)) {
                return Result.of(Status.ESCAPE);
            }
            // 必须先看大小再读：64 MiB + 1 的源文件在 48 MiB 堆下会先死在 readAllBytes，
            // 规范要求的 PXL002 根本没机会抛（R04）。
            // 但这条上限只属于 **PXL 文档**：PNG 资源不适用（发布态复核 F3）。
            if (Files.size(real) > Validator.MAX_DOC_BYTES) {
                boolean png = looksLikePngFile(real);
                // PNG 资源不受 PXL 文档大小上限约束；当调用方声明 pxl 时，
                // 只返回签名信息，避免为了报 PXL009 先把整张大 PNG 读入内存。
                if ("pxl".equals(expectedKind) && png) {
                    return Result.tooLarge(true);
                }
                // 声明为 png 的大非 PNG 资源也要在读盘前交给调用方报 PXL009。
                if ("png".equals(expectedKind) && !png) {
                    return Result.tooLarge(false);
                }
                if (!png) {
                    return Result.tooLarge(false);
                }
            }
            return Result.ok(Files.readAllBytes(real));
        } catch (NoSuchFileException missing) {
            // 悬垂的外链仍必须报 ESCAPE，与其目标存在时相同。
            return Result.of(danglingEscapes(current) ? Status.ESCAPE : Status.MISSING);
        } catch (NotDirectoryException notDirectory) {
            return Result.of(Status.MISSING);
        } catch (java.nio.file.FileSystemLoopException loop) {
            return Result.of(Status.MISSING);
        } catch (IOException failure) {
            // §8.4：权限、锁等宿主读取失败不能变成依赖缺失或文档超限。
            throw new UncheckedIOException(failure);
        }
    }

    /** 悬垂的符号链接是否指向 root 之外（按链接文本做**词法**判断，不需要目标存在）。 */
    private boolean danglingEscapes(Path path) {
        try {
            if (!Files.isSymbolicLink(path)) {
                return false;
            }
            Path target = Files.readSymbolicLink(path);
            Path resolved = target.isAbsolute()
                    ? target.normalize() : path.getParent().resolve(target).normalize();
            return !resolved.startsWith(root);
        } catch (NoSuchFileException missing) {
            return false;
        } catch (IOException failure) {
            throw new UncheckedIOException(failure);
        }
    }

    /** 只读前 8 字节判 PNG 签名：PNG 资源不受 PXL 文档字节上限约束。 */
    private static boolean looksLikePngFile(Path file) throws IOException {
        try (java.io.InputStream in = Files.newInputStream(file)) {
            byte[] head = new byte[8];
            int read = 0;
            while (read < head.length) {
                int step = in.read(head, read, head.length - read);
                if (step < 0) {
                    return false;
                }
                read += step;
            }
            byte[] signature = {(byte) 0x89, 'P', 'N', 'G', 0x0D, 0x0A, 0x1A, 0x0A};
            return java.util.Arrays.equals(head, signature);
        }
    }
    /** 该路径的真实落点是否已经离开 asset root。 */
    private boolean escapesRoot(Path path) {
        try {
            return !path.toRealPath().startsWith(root);
        } catch (IOException missing) {
            return false;
        }
    }
    private static boolean hasExactChild(Path directory, String name) {
        List<String> children = list(directory);
        return children.contains(name);
    }

    private static boolean hasCaseInsensitiveChild(Path directory, String name) {
        for (String child : list(directory)) {
            if (child.equalsIgnoreCase(name)) {
                return true;
            }
        }
        return false;
    }

    private static List<String> list(Path directory) {
        List<String> out = new ArrayList<String>();
        DirectoryStream<Path> stream = null;
        try {
            stream = Files.newDirectoryStream(directory);
            for (Path item : stream) {
                out.add(item.getFileName().toString());
            }
        } catch (java.nio.file.DirectoryIteratorException failure) {
            // DirectoryStream 把迭代阶段的 IOException 包在运行时异常中。
            // 不能返回已枚举的部分结果，也不能让它绕过 CLI001 的 I/O 边界。
            throw new UncheckedIOException(failure.getCause());
        } catch (NoSuchFileException missing) {
            return out;
        } catch (NotDirectoryException notDirectory) {
            return out;
        } catch (IOException failure) {
            throw new UncheckedIOException(failure);
        } finally {
            if (stream != null) {
                try {
                    stream.close();
                } catch (IOException ignored) {
                    // 关闭失败不影响判定。
                }
            }
        }
        java.util.Collections.sort(out);
        return out;
    }
}
