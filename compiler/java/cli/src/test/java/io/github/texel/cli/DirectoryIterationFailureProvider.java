package io.github.texel.cli;

import java.io.IOException;
import java.net.URI;
import java.nio.channels.SeekableByteChannel;
import java.nio.file.AccessDeniedException;
import java.nio.file.AccessMode;
import java.nio.file.CopyOption;
import java.nio.file.DirectoryIteratorException;
import java.nio.file.DirectoryStream;
import java.nio.file.FileStore;
import java.nio.file.FileSystem;
import java.nio.file.LinkOption;
import java.nio.file.OpenOption;
import java.nio.file.Path;
import java.nio.file.PathMatcher;
import java.nio.file.WatchEvent;
import java.nio.file.WatchKey;
import java.nio.file.WatchService;
import java.nio.file.attribute.BasicFileAttributes;
import java.nio.file.attribute.FileAttribute;
import java.nio.file.attribute.FileAttributeView;
import java.nio.file.attribute.UserPrincipalLookupService;
import java.nio.file.spi.FileSystemProvider;
import java.util.Iterator;
import java.util.Map;
import java.util.Set;

/**
 * 子进程专用的默认 provider：真实目录打开并返回一个真实项后，注入迭代 I/O 故障。
 *
 * <p>通过 java.nio.file.spi.DefaultFileSystemProvider 安装；关闭故障属性时，
 * 相同路径包装和真实文件系统委托仍参与正常对照。生产代码不依赖本类。
 */
public final class DirectoryIterationFailureProvider extends FileSystemProvider {
    static final String FAILURE_PROPERTY = "texel.test.directoryIterationFailure";
    static final String FAILURE_MESSAGE = "directory iteration failed after one real entry";

    private final FileSystemProvider delegate;
    private final WrappedFileSystem fileSystem;

    /** JVM 默认 provider 加载器要求此构造器公开并接收原 provider。 */
    public DirectoryIterationFailureProvider(FileSystemProvider delegate) {
        this.delegate = delegate;
        this.fileSystem = new WrappedFileSystem(this, delegate.getFileSystem(URI.create("file:///")));
    }

    private static Path unwrap(Path path) {
        return path instanceof WrappedPath ? ((WrappedPath) path).delegate : path;
    }

    @Override public String getScheme() { return delegate.getScheme(); }
    @Override public FileSystem getFileSystem(URI uri) { return fileSystem; }
    @Override public Path getPath(URI uri) { return fileSystem.wrap(delegate.getPath(uri)); }
    @Override public FileSystem newFileSystem(URI uri, Map<String, ?> env) throws IOException {
        return delegate.newFileSystem(uri, env);
    }
    @Override public SeekableByteChannel newByteChannel(Path path, Set<? extends OpenOption> options,
            FileAttribute<?>... attributes) throws IOException {
        return delegate.newByteChannel(unwrap(path), options, attributes);
    }

    @Override public java.nio.channels.FileChannel newFileChannel(Path path,
            Set<? extends OpenOption> options, FileAttribute<?>... attributes) throws IOException {
        return delegate.newFileChannel(unwrap(path), options, attributes);
    }

    @Override public DirectoryStream<Path> newDirectoryStream(final Path directory,
            final DirectoryStream.Filter<? super Path> filter) throws IOException {
        final DirectoryStream<Path> real = delegate.newDirectoryStream(unwrap(directory),
                new DirectoryStream.Filter<Path>() {
                    public boolean accept(Path entry) throws IOException {
                        return filter.accept(fileSystem.wrap(entry));
                    }
                });
        final boolean fail = Boolean.getBoolean(FAILURE_PROPERTY);
        return new DirectoryStream<Path>() {
            public Iterator<Path> iterator() {
                final Iterator<Path> entries = real.iterator();
                return new Iterator<Path>() {
                    private boolean returnedEntry;

                    private void failAfterEntry() {
                        if (fail && returnedEntry) {
                            throw new DirectoryIteratorException(new AccessDeniedException(
                                    directory.toString(), null, FAILURE_MESSAGE));
                        }
                    }

                    public boolean hasNext() {
                        failAfterEntry();
                        return entries.hasNext();
                    }
                    public Path next() {
                        failAfterEntry();
                        Path entry = fileSystem.wrap(entries.next());
                        returnedEntry = true;
                        return entry;
                    }
                    public void remove() { entries.remove(); }
                };
            }
            public void close() throws IOException { real.close(); }
        };
    }

    @Override public void createDirectory(Path path, FileAttribute<?>... attributes) throws IOException {
        delegate.createDirectory(unwrap(path), attributes);
    }
    @Override public void delete(Path path) throws IOException { delegate.delete(unwrap(path)); }
    @Override public void copy(Path source, Path target, CopyOption... options) throws IOException {
        delegate.copy(unwrap(source), unwrap(target), options);
    }
    @Override public void move(Path source, Path target, CopyOption... options) throws IOException {
        delegate.move(unwrap(source), unwrap(target), options);
    }
    @Override public boolean isSameFile(Path first, Path second) throws IOException {
        return delegate.isSameFile(unwrap(first), unwrap(second));
    }
    @Override public boolean isHidden(Path path) throws IOException { return delegate.isHidden(unwrap(path)); }
    @Override public FileStore getFileStore(Path path) throws IOException {
        return delegate.getFileStore(unwrap(path));
    }
    @Override public void checkAccess(Path path, AccessMode... modes) throws IOException {
        delegate.checkAccess(unwrap(path), modes);
    }
    @Override public <V extends FileAttributeView> V getFileAttributeView(Path path, Class<V> type,
            LinkOption... options) {
        return delegate.getFileAttributeView(unwrap(path), type, options);
    }
    @Override public <A extends BasicFileAttributes> A readAttributes(Path path, Class<A> type,
            LinkOption... options) throws IOException {
        return delegate.readAttributes(unwrap(path), type, options);
    }
    @Override public Map<String, Object> readAttributes(Path path, String attributes,
            LinkOption... options) throws IOException {
        return delegate.readAttributes(unwrap(path), attributes, options);
    }
    @Override public void setAttribute(Path path, String attribute, Object value,
            LinkOption... options) throws IOException {
        delegate.setAttribute(unwrap(path), attribute, value, options);
    }

    /** 包装默认路径，使 Files 的调用确实经过本测试 provider。 */
    private static final class WrappedFileSystem extends FileSystem {
        private final DirectoryIterationFailureProvider provider;
        private final FileSystem delegate;

        WrappedFileSystem(DirectoryIterationFailureProvider provider, FileSystem delegate) {
            this.provider = provider;
            this.delegate = delegate;
        }
        Path wrap(Path path) { return path == null ? null : new WrappedPath(this, path); }
        Iterator<Path> wrapIterator(final Iterator<Path> entries) {
            return new Iterator<Path>() {
                public boolean hasNext() { return entries.hasNext(); }
                public Path next() { return wrap(entries.next()); }
                public void remove() { entries.remove(); }
            };
        }
        @Override public FileSystemProvider provider() { return provider; }
        @Override public void close() throws IOException { delegate.close(); }
        @Override public boolean isOpen() { return delegate.isOpen(); }
        @Override public boolean isReadOnly() { return delegate.isReadOnly(); }
        @Override public String getSeparator() { return delegate.getSeparator(); }
        @Override public Iterable<Path> getRootDirectories() {
            return new Iterable<Path>() {
                public Iterator<Path> iterator() { return wrapIterator(delegate.getRootDirectories().iterator()); }
            };
        }
        @Override public Iterable<FileStore> getFileStores() { return delegate.getFileStores(); }
        @Override public Set<String> supportedFileAttributeViews() { return delegate.supportedFileAttributeViews(); }
        @Override public Path getPath(String first, String... more) { return wrap(delegate.getPath(first, more)); }
        @Override public PathMatcher getPathMatcher(String pattern) {
            final PathMatcher matcher = delegate.getPathMatcher(pattern);
            return new PathMatcher() {
                public boolean matches(Path path) { return matcher.matches(unwrap(path)); }
            };
        }
        @Override public UserPrincipalLookupService getUserPrincipalLookupService() {
            return delegate.getUserPrincipalLookupService();
        }
        @Override public WatchService newWatchService() throws IOException { return delegate.newWatchService(); }
    }

    private static final class WrappedPath implements Path {
        private final WrappedFileSystem fileSystem;
        private final Path delegate;

        WrappedPath(WrappedFileSystem fileSystem, Path delegate) {
            this.fileSystem = fileSystem;
            this.delegate = delegate;
        }
        @Override public FileSystem getFileSystem() { return fileSystem; }
        @Override public boolean isAbsolute() { return delegate.isAbsolute(); }
        @Override public Path getRoot() { return fileSystem.wrap(delegate.getRoot()); }
        @Override public Path getFileName() { return fileSystem.wrap(delegate.getFileName()); }
        @Override public Path getParent() { return fileSystem.wrap(delegate.getParent()); }
        @Override public int getNameCount() { return delegate.getNameCount(); }
        @Override public Path getName(int index) { return fileSystem.wrap(delegate.getName(index)); }
        @Override public Path subpath(int begin, int end) { return fileSystem.wrap(delegate.subpath(begin, end)); }
        @Override public boolean startsWith(Path path) { return delegate.startsWith(unwrap(path)); }
        @Override public boolean startsWith(String path) { return delegate.startsWith(path); }
        @Override public boolean endsWith(Path path) { return delegate.endsWith(unwrap(path)); }
        @Override public boolean endsWith(String path) { return delegate.endsWith(path); }
        @Override public Path normalize() { return fileSystem.wrap(delegate.normalize()); }
        @Override public Path resolve(Path path) { return fileSystem.wrap(delegate.resolve(unwrap(path))); }
        @Override public Path resolve(String path) { return fileSystem.wrap(delegate.resolve(path)); }
        @Override public Path resolveSibling(Path path) { return fileSystem.wrap(delegate.resolveSibling(unwrap(path))); }
        @Override public Path resolveSibling(String path) { return fileSystem.wrap(delegate.resolveSibling(path)); }
        @Override public Path relativize(Path path) { return fileSystem.wrap(delegate.relativize(unwrap(path))); }
        @Override public URI toUri() { return delegate.toUri(); }
        @Override public Path toAbsolutePath() { return fileSystem.wrap(delegate.toAbsolutePath()); }
        @Override public Path toRealPath(LinkOption... options) throws IOException {
            return fileSystem.wrap(delegate.toRealPath(options));
        }
        @Override public java.io.File toFile() { return new java.io.File(delegate.toString()); }
        @Override public WatchKey register(WatchService watcher, WatchEvent.Kind<?>[] events,
                WatchEvent.Modifier... modifiers) throws IOException {
            return delegate.register(watcher, events, modifiers);
        }
        @Override public WatchKey register(WatchService watcher, WatchEvent.Kind<?>... events) throws IOException {
            return delegate.register(watcher, events);
        }
        @Override public Iterator<Path> iterator() { return fileSystem.wrapIterator(delegate.iterator()); }
        @Override public int compareTo(Path path) { return delegate.compareTo(unwrap(path)); }
        @Override public boolean equals(Object other) {
            return other instanceof WrappedPath && fileSystem == ((WrappedPath) other).fileSystem
                    && delegate.equals(((WrappedPath) other).delegate);
        }
        @Override public int hashCode() { return delegate.hashCode(); }
        @Override public String toString() { return delegate.toString(); }
    }
}
