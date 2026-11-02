/* SPDX-License-Identifier: BSD-2-Clause-Patent */
/* Android side of the iOS Data file (UniXNU-Pagani-Drivers/drivers/PaganiPersistentData/DataFileFormat.h). Run as root on the phone
 * (adb shell, then su; or TWRP with /data decrypted). Build: build-android.sh (static aarch64, NDK).
 *
 *   unixnu-datafile probe                                   read only: /data, dm mapping, partitions, f2fs geometry, slots, file
 *   unixnu-datafile verify                                  read only: pin flag and GC failures, immutable, FIEMAP == descriptor,
 *                                                           every canary, slots. Exit 0 only when everything holds
 *   unixnu-datafile create --size-gib N --confirm SERIAL:N  make the pinned file, prove its mapping, write canaries, make it immutable,
 *                                                           publish the descriptor
 *   unixnu-datafile publish --confirm SERIAL:publish        re-activate an intact file (after a revoke or a root re-flash)
 *   unixnu-datafile revoke --confirm SERIAL:revoke          newest descriptor = REVOKED (XNU refuses the file); no userdata writes
 *   unixnu-datafile guard                                   for a boot service: verify, and revoke when it fails (no confirmation)
 *   unixnu-datafile selftest-check [--blocks K]             read only: the last K data blocks hold XNU's write self-test pattern
 *   unixnu-datafile remove --confirm SERIAL:remove          revoke, clear immutable, delete the file
 * Options: --file PATH (default /data/unixnu/ios-data.img), --min-free-gib N (create: /data keeps at least this much, default 48),
 *          --quiet.
 *
 * Writes, in order, and only in create/publish/revoke/guard/remove:
 *   1. through f2fs: the new file (pin, fallocate), then 4 KiB at the start of each of its 2 MiB segments (the mapping proof);
 *   2. raw, /dev/block/by-name/userdata: block 0 of each of the file's segments (the canary), only blocks that step 1 proved to be
 *      the file's own (the raw block changed when f2fs wrote that file block in place);
 *   3. the inode flag FS_IMMUTABLE_FL (no unlink, rename, truncate or write through the file system, root included);
 *   4. raw, /dev/block/by-name/unixnu: the descriptor slot (blocks 16..255 of the iOS partition), table first, header last.
 * Every check comes first; any failure stops before the next write. Nothing else on the device is written. */
#define _GNU_SOURCE
#include <ctype.h>
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <limits.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <sys/statfs.h>
#include <sys/sysmacros.h>
#include <sys/types.h>
#include <time.h>
#include <unistd.h>
#include <linux/fiemap.h>
#include <linux/fs.h>
#include "DfSha256.h"
#include "DataFileFormat.h"
#include "DataFileBuild.h"
#ifdef __ANDROID__
#include <sys/system_properties.h>
#endif

#ifndef F2FS_IOC_SET_PIN_FILE
#define F2FS_IOC_SET_PIN_FILE _IOW (0xf5, 13, uint32_t)
#define F2FS_IOC_GET_PIN_FILE _IOR (0xf5, 14, uint32_t)
#endif
#define SEG_BYTES ((uint64_t) DF_SEG_BLOCKS * DF_BLOCK)
#define F2FS_MAGIC 0xF2F52010u

static const char *g_file = "/data/unixnu/ios-data.img";
static const char *g_userdata = "/dev/block/by-name/userdata";
static const char *g_unixnu = "/dev/block/by-name/unixnu";
static int g_quiet;

static void stop (const char *fmt, ...)
{
  int e = errno;
  va_list ap;
  va_start (ap, fmt);
  fprintf (stderr, "STOP: ");
  vfprintf (stderr, fmt, ap);
  if (e) fprintf (stderr, " (errno %d: %s)", e, strerror (e));
  fprintf (stderr, "\n");
  va_end (ap);
  exit (2);
}
static void say (const char *fmt, ...)
{
  if (g_quiet) return;
  va_list ap;
  va_start (ap, fmt);
  vprintf (fmt, ap);
  va_end (ap);
}

static int read_text (const char *path, char *buf, size_t n)
{
  FILE *f = fopen (path, "re");
  if (!f) return 0;
  size_t got = fread (buf, 1, n - 1, f);
  fclose (f);
  buf[got] = 0;
  while (got && isspace ((unsigned char) buf[got - 1])) buf[--got] = 0;
  return 1;
}
static uint64_t read_u64 (const char *path)
{
  char b[64], *end;
  errno = 0;
  if (!read_text (path, b, sizeof b)) stop ("cannot read %s", path);
  unsigned long long v = strtoull (b, &end, 10);
  if (end == b || *end) { errno = 0; stop ("not a number in %s: %s", path, b); }
  return v;
}
static void *block_alloc (size_t bytes)
{
  void *p = NULL;
  if (posix_memalign (&p, DF_BLOCK, bytes)) { errno = 0; stop ("out of memory"); }
  memset (p, 0, bytes);
  return p;
}
static void random_bytes (uint8_t *p, size_t n)
{
  int fd = open ("/dev/urandom", O_RDONLY | O_CLOEXEC);
  if (fd < 0 || read (fd, p, n) != (ssize_t) n) stop ("/dev/urandom");
  close (fd);
}
static void serial_of_device (char *out, size_t n)
{
  out[0] = 0;
#ifdef __ANDROID__
  char v[PROP_VALUE_MAX] = "";
  __system_property_get ("ro.serialno", v);
  if (!v[0]) __system_property_get ("ro.boot.serialno", v);
  snprintf (out, n, "%s", v);
#else
  const char *e = getenv ("UNIXNU_SERIAL");
  snprintf (out, n, "%s", e ? e : "");
#endif
  if (!out[0]) { errno = 0; stop ("no ro.serialno"); }
}
static void require_confirm (const char *given, const char *what)
{
  char serial[96], want[160];
  serial_of_device (serial, sizeof serial);
  snprintf (want, sizeof want, "%s:%s", serial, what);
  if (!given || strcmp (given, want)) { errno = 0; stop ("confirmation required: --confirm %s", want); }
}

/* ---- the device: /data, its dm mapping, userdata and unixnu, the f2fs geometry ---------------------------------------------- */
typedef struct {
  char data_dev[256], data_opts[1024], dm[64], raw[64], unixnu[64], disk[64];
  uint64_t part_first, part_last, unixnu_first, unixnu_last, fs_main, fs_blocks, fs_free, fs_size, pin_threshold;
  int lun;
} env_t;

static void part_of (const char *bynamePath, char *name, size_t n, uint64_t *first, uint64_t *last, char *disk, size_t dn)
{
  char real[PATH_MAX], sys[PATH_MAX + 64], link[PATH_MAX];
  errno = 0;
  if (!realpath (bynamePath, real)) stop ("%s", bynamePath);
  const char *base = strrchr (real, '/');
  snprintf (name, n, "%s", base ? base + 1 : real);
  snprintf (sys, sizeof sys, "/sys/class/block/%s/start", name);
  uint64_t start = read_u64 (sys);
  snprintf (sys, sizeof sys, "/sys/class/block/%s/size", name);
  uint64_t size = read_u64 (sys);
  /* sysfs counts 512-byte sectors whatever the logical block size is. */
  if (start % 8 || size % 8 || !size) { errno = 0; stop ("%s is not on 4 KiB boundaries", name); }
  *first = start / 8;
  *last = start / 8 + size / 8 - 1;
  snprintf (sys, sizeof sys, "/sys/class/block/%s", name);
  if (!realpath (sys, link)) stop ("%s", sys);
  char *slash = strrchr (link, '/');
  if (!slash) { errno = 0; stop ("sysfs path %s", link); }
  *slash = 0;
  const char *parent = strrchr (link, '/');
  snprintf (disk, dn, "%s", parent ? parent + 1 : link);
}

static void read_superblock (const char *dev, env_t *e)
{
  uint8_t *b = block_alloc (DF_BLOCK);
  int fd = open (dev, O_RDONLY | O_CLOEXEC);
  errno = 0;
  if (fd < 0 || pread (fd, b, DF_BLOCK, 0) != DF_BLOCK) stop ("read the f2fs superblock of %s", dev);
  close (fd);
  const uint8_t *s = b + 1024;
  errno = 0;
  if (df_get32 (s) != F2FS_MAGIC) stop ("%s: not f2fs", dev);
  uint32_t log_bs = df_get32 (s + 16), log_bps = df_get32 (s + 20), sps = df_get32 (s + 24);
  if (log_bs != 12 || log_bps != 9 || sps != 1)
    stop ("f2fs layout %u/%u/%u not supported (4 KiB blocks, 512 per segment, 1 segment per section)", log_bs, log_bps, sps);
  if (s[2201] || (df_get32 (s + 2180) & 2)) stop ("multi-device or zoned f2fs is not supported");
  e->fs_blocks = df_get64 (s + 36);
  e->fs_main = df_get32 (s + 104);
  free (b);
}

static void discover (env_t *e)
{
  memset (e, 0, sizeof *e);
  FILE *m = fopen ("/proc/mounts", "re");
  errno = 0;
  if (!m) stop ("/proc/mounts");
  char line[2048];
  unsigned mounts = 0;
  while (fgets (line, sizeof line, m)) {
    char dev[256], mnt[256], type[32], opts[1024];
    if (sscanf (line, "%255s %255s %31s %1023s", dev, mnt, type, opts) == 4 && !strcmp (mnt, "/data")) {
      if (strcmp (type, "f2fs")) stop ("/data is %s, not f2fs", type);
      snprintf (e->data_dev, sizeof e->data_dev, "%s", dev);
      snprintf (e->data_opts, sizeof e->data_opts, "%s", opts);
      ++mounts;
    }
  }
  fclose (m);
  errno = 0;
  if (!mounts) stop ("/data is not mounted");
  struct stat st;
  char sys[PATH_MAX + 64], real[PATH_MAX], txt[256];
  if (stat (e->data_dev, &st)) stop ("%s", e->data_dev);
  snprintf (sys, sizeof sys, "/sys/dev/block/%u:%u", major (st.st_rdev), minor (st.st_rdev));
  if (!realpath (sys, real)) stop ("%s", sys);
  snprintf (e->dm, sizeof e->dm, "%s", strrchr (real, '/') + 1);
  part_of (g_userdata, e->raw, sizeof e->raw, &e->part_first, &e->part_last, e->disk, sizeof e->disk);
  /* /data must be the raw partition or one dm target over all of it (dm-default-key), so FIEMAP offsets are partition offsets.
     The mapping proof of `create` checks it block by block as well. */
  if (strcmp (e->dm, e->raw)) {
    snprintf (sys, sizeof sys, "/sys/block/%s/slaves", e->dm);
    DIR *d = opendir (sys);
    if (!d) stop ("%s", sys);
    int slaves = 0, match = 0;
    struct dirent *de;
    while ((de = readdir (d))) {
      if (de->d_name[0] == '.') continue;
      ++slaves;
      match |= !strcmp (de->d_name, e->raw);
    }
    closedir (d);
    errno = 0;
    if (slaves != 1 || !match) stop ("%s is not a single mapping of %s", e->dm, e->raw);
    snprintf (sys, sizeof sys, "/sys/block/%s/size", e->dm);
    uint64_t dmSize = read_u64 (sys);
    if (dmSize != (e->part_last - e->part_first + 1) * 8) stop ("%s size %" PRIu64 " differs from %s", e->dm, dmSize, e->raw);
    snprintf (sys, sizeof sys, "/sys/block/%s/dm/name", e->dm);
    if (read_text (sys, txt, sizeof txt)) say ("dm: %s name=%s over %s\n", e->dm, txt, e->raw);
  }
  char disk2[64];
  part_of (g_unixnu, e->unixnu, sizeof e->unixnu, &e->unixnu_first, &e->unixnu_last, disk2, sizeof disk2);
  errno = 0;
  if (strcmp (disk2, e->disk)) stop ("userdata (%s) and unixnu (%s) are on different disks", e->disk, disk2);
  snprintf (sys, sizeof sys, "/sys/block/%s/queue/logical_block_size", e->disk);
  if (read_u64 (sys) != DF_BLOCK) stop ("%s: logical block size is not 4096", e->disk);
  snprintf (sys, sizeof sys, "/sys/block/%s/device", e->disk);
  if (!realpath (sys, real)) stop ("%s", sys);
  const char *colon = strrchr (real, ':');
  e->lun = colon ? atoi (colon + 1) : -1;
  errno = 0;
  if (e->lun != 0) stop ("%s is SCSI LUN %d, XNU expects LUN 0", e->disk, e->lun);
  if (e->unixnu_first <= e->part_last && e->part_first <= e->unixnu_last) stop ("userdata and unixnu overlap");
  if (e->part_last > 0xffffffffULL || e->unixnu_last > 0xffffffffULL) stop ("partitions beyond READ(10)/WRITE(10) reach");
  read_superblock (e->data_dev, e);
  errno = 0;
  if (e->fs_blocks > e->part_last - e->part_first + 1 || e->fs_main >= e->fs_blocks) stop ("f2fs geometry outside the partition");
  snprintf (sys, sizeof sys, "/sys/fs/f2fs/%s/main_blkaddr", e->dm);
  if (read_text (sys, txt, sizeof txt) && strtoull (txt, NULL, 10) != e->fs_main) stop ("sysfs main_blkaddr %s differs from the superblock", txt);
  snprintf (sys, sizeof sys, "/sys/fs/f2fs/%s/gc_pin_file_thresh", e->dm);
  if (read_text (sys, txt, sizeof txt)) e->pin_threshold = strtoull (txt, NULL, 10);
  struct statfs sf;
  if (statfs ("/data", &sf)) stop ("statfs /data");
  e->fs_free = (uint64_t) sf.f_bavail * sf.f_bsize;
  e->fs_size = (uint64_t) sf.f_blocks * sf.f_bsize;
}

/* The unixnu partition must be the root image as userdata.py lays it out: the SHNGROOT manifest in block 0 (its SHA-256 at 4064) and
   every window at or past 1 MiB, so blocks 16..255 are padding. Returns 1 when that holds. */
static int unixnu_layout_ok (int quiet)
{
  uint8_t *b = block_alloc (DF_BLOCK), digest[32];
  int fd = open (g_unixnu, O_RDONLY | O_DIRECT | O_CLOEXEC);
  errno = 0;
  if (fd < 0 || pread (fd, b, DF_BLOCK, 0) != DF_BLOCK) stop ("read unixnu block 0");
  close (fd);
  int ok = !memcmp (b, "SHNGROOT", 8) && df_get32 (b + 8) == 1 && df_get32 (b + 12) >= 1 && df_get32 (b + 12) <= 4;
  if (ok) {
    df_sha256 (b, DF_HASHED, digest);
    ok = !memcmp (digest, b + DF_HASHED, 32);
  }
  for (uint32_t i = 0; ok && i < df_get32 (b + 12); ++i)
    if (df_get64 (b + 24 + 128 * i + 32) < (uint64_t) DF_SLOTS_END * DF_BLOCK) ok = 0;
  if (!ok && !quiet) say ("unixnu: block 0 is not a root manifest with every window past 1 MiB\n");
  free (b);
  return ok;
}

/* ---- slots ------------------------------------------------------------------------------------------------------------------ */
typedef struct {
  uint8_t *block[2];       /* header blocks of slot A and B */
  df_header h[2];
  int rc[2];
} slots_t;

static void slots_read (slots_t *s)
{
  int fd = open (g_unixnu, O_RDONLY | O_DIRECT | O_CLOEXEC);
  errno = 0;
  if (fd < 0) stop ("open %s", g_unixnu);
  for (int i = 0; i < 2; ++i) {
    s->block[i] = block_alloc (DF_BLOCK);
    if (pread (fd, s->block[i], DF_BLOCK, (off_t) (i ? DF_SLOT_B : DF_SLOT_A) * DF_BLOCK) != DF_BLOCK) stop ("read slot %c", 'A' + i);
    s->rc[i] = df_header_decode (s->block[i], &s->h[i], df_sha256);
  }
  close (fd);
}
/* Newest valid slot (any state), or -1. */
static int slots_newest (const slots_t *s)
{
  int best = -1;
  for (int i = 0; i < 2; ++i)
    if (s->rc[i] == DF_OK && (best < 0 || s->h[i].generation > s->h[best].generation)) best = i;
  return best;
}
/* Newest ACTIVE/READONLY slot, ignoring a newer REVOKED one, or -1. */
static int slots_newest_live (const slots_t *s)
{
  int best = -1;
  for (int i = 0; i < 2; ++i)
    if (s->rc[i] == DF_OK && s->h[i].state != DF_STATE_REVOKED && (best < 0 || s->h[i].generation > s->h[best].generation)) best = i;
  return best;
}
static uint8_t *slot_table (int slot, const df_header *h)
{
  uint32_t blocks = (uint32_t) (((uint64_t) h->extent_count * DF_EXTENT_BYTES + DF_BLOCK - 1) / DF_BLOCK);
  uint8_t *t = block_alloc ((size_t) blocks * DF_BLOCK);
  int fd = open (g_unixnu, O_RDONLY | O_DIRECT | O_CLOEXEC);
  errno = 0;
  if (fd < 0 || pread (fd, t, (size_t) blocks * DF_BLOCK, (off_t) ((slot ? DF_SLOT_B : DF_SLOT_A) + 1) * DF_BLOCK) != (ssize_t) blocks * DF_BLOCK)
    stop ("read the extent table");
  close (fd);
  return t;
}
/* Every block of the slot region is zero or one of our slots: nothing foreign would be overwritten. */
static void slots_region_ok (const slots_t *s)
{
  uint8_t *r = block_alloc ((size_t) (DF_SLOTS_END - DF_SLOT_A) * DF_BLOCK);
  int fd = open (g_unixnu, O_RDONLY | O_DIRECT | O_CLOEXEC);
  errno = 0;
  if (fd < 0 || pread (fd, r, (size_t) (DF_SLOTS_END - DF_SLOT_A) * DF_BLOCK, (off_t) DF_SLOT_A * DF_BLOCK) != (ssize_t) (DF_SLOTS_END - DF_SLOT_A) * DF_BLOCK)
    stop ("read unixnu blocks 16..255");
  close (fd);
  for (int i = 0; i < 2; ++i) {
    if (s->rc[i] == DF_OK) continue;
    const uint8_t *slot = r + (size_t) (i ? DF_SLOT_B - DF_SLOT_A : 0) * DF_BLOCK;
    for (size_t k = 0; k < (size_t) DF_SLOT_BLOCKS * DF_BLOCK; ++k)
      if (slot[k] && s->rc[i] != DF_ERR_HASH) { errno = 0; stop ("unixnu slot %c holds foreign data (%s); not overwriting it", 'A' + i, df_error_name (s->rc[i])); }
  }
  free (r);
}
/* Write `blocks` slot blocks (table blocks first, header block last), read them back. Only unixnu blocks 16..255 are ever written. */
static void slot_write (int slot, const uint8_t *image, uint32_t blocks)
{
  const uint64_t first = slot ? DF_SLOT_B : DF_SLOT_A;
  errno = 0;
  if (!blocks || blocks > DF_SLOT_BLOCKS || first + blocks > DF_SLOTS_END) stop ("slot write outside blocks 16..255");
  if (!unixnu_layout_ok (0)) stop ("the unixnu layout leaves no room for the slots");
  int fd = open (g_unixnu, O_RDWR | O_DIRECT | O_DSYNC | O_CLOEXEC);
  if (fd < 0) stop ("open %s for writing", g_unixnu);
  uint8_t *back = block_alloc ((size_t) blocks * DF_BLOCK);
  if (blocks > 1 && pwrite (fd, image + DF_BLOCK, (size_t) (blocks - 1) * DF_BLOCK, (off_t) (first + 1) * DF_BLOCK) != (ssize_t) (blocks - 1) * DF_BLOCK)
    stop ("write the extent table");
  if (fsync (fd)) stop ("flush the extent table");
  if (pwrite (fd, image, DF_BLOCK, (off_t) first * DF_BLOCK) != DF_BLOCK || fsync (fd)) stop ("write the descriptor header");
  if (pread (fd, back, (size_t) blocks * DF_BLOCK, (off_t) first * DF_BLOCK) != (ssize_t) blocks * DF_BLOCK ||
      memcmp (back, image, (size_t) blocks * DF_BLOCK))
    stop ("descriptor read-back differs");
  close (fd);
  free (back);
}

/* ---- the file --------------------------------------------------------------------------------------------------------------- */
static unsigned file_flags (int fd)
{
  unsigned flags = 0;   /* FS_IOC_GETFLAGS moves an int, whatever its _IOR size says */
  if (ioctl (fd, FS_IOC_GETFLAGS, &flags)) stop ("FS_IOC_GETFLAGS");
  return flags;
}
/* 1 when f2fs keeps the file pinned (it reports FI_PIN_FILE as FS_NOCOW_FL); *gcFailures: the times GC tried to move a block of it.
   f2fs unpins a file once that count reaches gc_pin_file_thresh (default 2048) and may then move it: any count > 0 is a failure. */
static int is_pinned (int fd, uint32_t *gcFailures)
{
  uint32_t pin = 0;
  if (ioctl (fd, F2FS_IOC_GET_PIN_FILE, &pin)) stop ("F2FS_IOC_GET_PIN_FILE");
  *gcFailures = pin;
  return (file_flags (fd) & FS_NOCOW_FL) != 0;
}
static void set_immutable (int fd, int on)
{
  unsigned flags = file_flags (fd);
  unsigned want = on ? flags | FS_IMMUTABLE_FL : flags & ~(unsigned) FS_IMMUTABLE_FL;
  want &= ~(unsigned) (FS_NOCOW_FL | FS_ENCRYPT_FL | FS_INLINE_DATA_FL | FS_VERITY_FL);   /* reported, not settable */
  if (want != (flags & ~(unsigned) (FS_NOCOW_FL | FS_ENCRYPT_FL | FS_INLINE_DATA_FL | FS_VERITY_FL)) && ioctl (fd, FS_IOC_SETFLAGS, &want))
    stop ("FS_IOC_SETFLAGS (immutable %s)", on ? "on" : "off");
  if (!!(file_flags (fd) & FS_IMMUTABLE_FL) != !!on) { errno = 0; stop ("the immutable flag did not change"); }
}

/* FIEMAP of the whole file -> segment extents in file order. */
static df_extent *file_extents (int fd, uint64_t size, const env_t *e, uint32_t *count)
{
  const uint32_t per = 2048;
  struct fiemap *fm = calloc (1, sizeof *fm + per * sizeof (struct fiemap_extent));
  df_fiemap_extent *all = NULL;
  uint32_t n = 0, cap = 0, last = 0;
  uint64_t start = 0;
  errno = 0;
  if (!fm) stop ("out of memory");
  while (!last) {
    memset (fm, 0, sizeof *fm + per * sizeof (struct fiemap_extent));
    fm->fm_start = start;
    fm->fm_length = size - start;
    fm->fm_flags = FIEMAP_FLAG_SYNC;
    fm->fm_extent_count = per;
    errno = 0;
    if (ioctl (fd, FS_IOC_FIEMAP, fm)) stop ("FS_IOC_FIEMAP");
    if (!fm->fm_mapped_extents) { errno = 0; stop ("FIEMAP returned no extent at %" PRIu64, start); }
    for (uint32_t i = 0; i < fm->fm_mapped_extents; ++i) {
      struct fiemap_extent *x = &fm->fm_extents[i];
      if (n == cap) {
        cap = cap ? cap * 2 : 4096;
        all = realloc (all, cap * sizeof *all);
        if (!all) stop ("out of memory");
      }
      all[n].logical = x->fe_logical;
      all[n].physical = x->fe_physical;
      all[n].length = x->fe_length;
      all[n].flags = x->fe_flags & ~FIEMAP_EXTENT_LAST;
      ++n;
      start = x->fe_logical + x->fe_length;
      if ((x->fe_flags & FIEMAP_EXTENT_LAST) || start >= size) last = 1;
    }
  }
  /* f2fs marks LAST only when it saw the end of the file inside the queried range; the converter wants exactly the final one. */
  all[n - 1].flags |= DF_FIEMAP_LAST;
  df_extent *out = calloc (DF_MAX_EXTENTS, sizeof *out);
  uint32_t bad = 0;
  if (!out) stop ("out of memory");
  int rc = df_from_fiemap (all, n, size, e->fs_main, e->fs_blocks, out, DF_MAX_EXTENTS, count, &bad);
  errno = 0;
  if (rc) stop ("FIEMAP extent %u (of %u): %s", bad, n, df_fiemap_error_name (rc));
  free (all);
  free (fm);
  return out;
}

static int same_extents (const df_extent *a, uint32_t na, const df_extent *b, uint32_t nb)
{
  if (na != nb) return 0;
  for (uint32_t i = 0; i < na; ++i) if (a[i].first_rel != b[i].first_rel || a[i].segments != b[i].segments) return 0;
  return 1;
}

/* Partition-relative block 0 of every segment, in file order. */
static uint64_t *segment_blocks (const df_extent *x, uint32_t n, uint64_t segments)
{
  uint64_t *s = malloc (segments * sizeof *s), k = 0;
  if (!s) stop ("out of memory");
  for (uint32_t i = 0; i < n; ++i)
    for (uint32_t j = 0; j < x[i].segments; ++j) s[k++] = x[i].first_rel + (uint64_t) j * DF_SEG_BLOCKS;
  if (k != segments) { errno = 0; stop ("segment count"); }
  return s;
}

static uint64_t fnv64 (const uint8_t *p, size_t n)
{
  uint64_t h = 1469598103934665603ULL;
  for (size_t i = 0; i < n; ++i) h = (h ^ p[i]) * 1099511628211ULL;
  return h;
}

/* Canaries: count the segments whose block 0 is not the expected canary. */
static uint64_t canaries_bad (const df_header *h, const uint64_t *seg, uint64_t segments, uint64_t *firstBad)
{
  int raw = open (g_userdata, O_RDONLY | O_DIRECT | O_CLOEXEC);
  errno = 0;
  if (raw < 0) stop ("open %s", g_userdata);
  uint8_t *got = block_alloc (DF_BLOCK), *want = block_alloc (DF_BLOCK);
  uint64_t bad = 0;
  *firstBad = ~0ULL;
  for (uint64_t s = 0; s < segments; ++s) {
    if (pread (raw, got, DF_BLOCK, (off_t) (seg[s] * DF_BLOCK)) != DF_BLOCK) stop ("raw read of segment %" PRIu64, s);
    df_canary_block (h, s, seg[s], want, df_sha256);
    if (memcmp (got, want, DF_BLOCK)) { if (!bad++) *firstBad = s; }
  }
  close (raw);
  free (got);
  free (want);
  return bad;
}

/* ---- commands --------------------------------------------------------------------------------------------------------------- */
static void print_env (const env_t *e)
{
  say ("data: %s (%s) opts=%s\n", e->data_dev, e->dm, e->data_opts);
  say ("userdata: %s on %s LUN %d, LBA %" PRIu64 "..%" PRIu64 "\n", e->raw, e->disk, e->lun, e->part_first, e->part_last);
  say ("unixnu: %s, LBA %" PRIu64 "..%" PRIu64 "\n", e->unixnu, e->unixnu_first, e->unixnu_last);
  say ("f2fs: main_blkaddr %" PRIu64 ", block_count %" PRIu64 ", free %.2f GiB of %.2f GiB, gc_pin_file_thresh %" PRIu64 "\n", e->fs_main,
       e->fs_blocks, e->fs_free / 1073741824.0, e->fs_size / 1073741824.0, e->pin_threshold);
}
static void print_slots (const slots_t *s)
{
  for (int i = 0; i < 2; ++i) {
    if (s->rc[i] != DF_OK) { say ("slot %c: %s\n", 'A' + i, df_error_name (s->rc[i])); continue; }
    const df_header *h = &s->h[i];
    say ("slot %c: generation %" PRIu64 " state %u extents %u segments %" PRIu64 " data %.2f GiB ino %" PRIu64 "\n", 'A' + i,
         h->generation, h->state, h->extent_count, h->segment_count, h->data_blocks * 4096.0 / 1073741824.0, h->file_ino);
  }
}
static void print_disk_limit (const df_header *h)
{
  const uint64_t pages = df_max_disk_pages (h->data_blocks);
  say ("Data seed: at most %" PRIu64 " pages (%.3f GiB) without a head; build it a little smaller (whole GiB below this)\n", pages,
       pages * 4096.0 / 1073741824.0);
}

/* The descriptor in force and its table, checked against the device as it is now. Returns the slot, or stops. */
static int live_descriptor (const env_t *e, slots_t *s, int allowRevoked, df_header *h, df_extent **ext)
{
  int slot = allowRevoked ? slots_newest_live (s) : slots_newest (s);
  errno = 0;
  if (slot < 0) stop ("no descriptor in unixnu");
  *h = s->h[slot];
  if (h->state == DF_STATE_REVOKED) stop ("the newest descriptor is REVOKED (publish re-activates an intact file)");
  if (df_check_partition (h, e->part_first, e->part_last)) stop ("userdata moved since the descriptor was made");
  if (h->fs_main != e->fs_main || h->fs_blocks != e->fs_blocks) stop ("f2fs geometry changed since the descriptor was made");
  uint8_t *t = slot_table (slot, h);
  *ext = calloc (h->extent_count, sizeof **ext);
  uint32_t *idx = calloc (h->extent_count, sizeof *idx);
  if (!*ext || !idx) stop ("out of memory");
  int rc = df_table_parse (h, t, *ext, idx, df_sha256);
  errno = 0;
  if (rc) stop ("descriptor table: %s", df_error_name (rc));
  free (t);
  free (idx);
  return slot;
}

/* Everything verify checks, without printing a verdict: 0 when the file is intact. */
static int check_file (const env_t *e, const df_header *h, const df_extent *ext, int quietCanaries)
{
  int fd = open (g_file, O_RDONLY | O_CLOEXEC);
  if (fd < 0) { say ("file: %s missing\n", g_file); return 1; }
  struct stat st;
  uint32_t gc = 0;
  int bad = 0;
  if (fstat (fd, &st)) stop ("fstat");
  if ((uint64_t) st.st_size != h->file_bytes) { say ("file: size %lld, descriptor %" PRIu64 "\n", (long long) st.st_size, h->file_bytes); bad = 1; }
  if (!is_pinned (fd, &gc)) { say ("file: NOT pinned\n"); bad = 1; }
  if (gc) { say ("file: f2fs GC tried to move it %u times (it unpins at gc_pin_file_thresh): recreate it\n", gc); bad = 1; }
  if (!(file_flags (fd) & FS_IMMUTABLE_FL)) { say ("file: not immutable\n"); bad = 1; }
  if ((uint64_t) st.st_ino != h->file_ino) { say ("file: inode %llu, descriptor %" PRIu64 " (a different file)\n", (unsigned long long) st.st_ino, h->file_ino); bad = 1; }
  if (!bad) {
    uint32_t n = 0;
    df_extent *now = file_extents (fd, (uint64_t) st.st_size, e, &n);
    if (!same_extents (now, n, ext, h->extent_count)) { say ("file: FIEMAP differs from the descriptor (the file moved)\n"); bad = 1; }
    free (now);
  }
  close (fd);
  if (!bad) {
    uint64_t *seg = segment_blocks (ext, h->extent_count, h->segment_count), first = 0;
    uint64_t nbad = canaries_bad (h, seg, h->segment_count, &first);
    if (nbad) { say ("canaries: %" PRIu64 " of %" PRIu64 " wrong, first segment %" PRIu64 "\n", nbad, h->segment_count, first); bad = 1; }
    else if (!quietCanaries) say ("canaries: all %" PRIu64 " intact\n", h->segment_count);
    free (seg);
  }
  return bad;
}

static int cmd_verify (void)
{
  env_t e;
  slots_t s;
  df_header h;
  df_extent *ext = NULL;
  discover (&e);
  print_env (&e);
  slots_read (&s);
  print_slots (&s);
  live_descriptor (&e, &s, 0, &h, &ext);
  print_disk_limit (&h);
  int bad = check_file (&e, &h, ext, 0);
  printf ("%s\n", bad ? "VERIFY FAILED" : "VERIFY OK");
  return bad;
}

static void revoke_newest (slots_t *s)
{
  int newest = slots_newest (s);
  if (newest < 0 || s->h[newest].state == DF_STATE_REVOKED) { printf ("nothing to revoke\n"); return; }
  uint8_t *blk = block_alloc (DF_BLOCK);
  df_build_revoked (&s->h[newest], s->h[newest].generation + 1, blk, df_sha256);
  const int target = newest == 0 ? 1 : 0;
  slot_write (target, blk, 1);
  printf ("REVOKED: generation %" PRIu64 " in slot %c\n", s->h[newest].generation + 1, 'A' + target);
  free (blk);
}

static int cmd_create (uint64_t sizeGiB, const char *confirm, uint64_t minFreeGiB)
{
  char what[32];
  snprintf (what, sizeof what, "%" PRIu64, sizeGiB);
  require_confirm (confirm, what);
  env_t e;
  slots_t s;
  discover (&e);
  print_env (&e);
  slots_read (&s);
  print_slots (&s);
  errno = 0;
  const uint64_t size = sizeGiB << 30;
  if (!sizeGiB || sizeGiB > 512) stop ("size %" PRIu64 " GiB", sizeGiB);
  if (strstr (e.data_opts, "checkpoint=disable")) stop ("/data has checkpointing disabled (an OTA is pending): finish or cancel it first");
  if (strstr (e.data_opts, "mode=lfs")) stop ("/data is mounted mode=lfs: f2fs refuses pinned files");
  if (access ("/sys/fs/f2fs/features/pin_file", F_OK)) stop ("this kernel's f2fs has no pin_file feature");
  if (e.fs_free < size || e.fs_free - size < (minFreeGiB << 30))
    stop ("/data has %.1f GiB free; %" PRIu64 " GiB for the file would leave less than %" PRIu64 " GiB", e.fs_free / 1073741824.0, sizeGiB, minFreeGiB);
  if (!unixnu_layout_ok (0)) stop ("unixnu does not hold a root image laid out by userdata.py: no room for the descriptor");
  slots_region_ok (&s);
  int live = slots_newest_live (&s);
  if (live >= 0) stop ("a descriptor is already published (generation %" PRIu64 "): remove the old file first", s.h[live].generation);
  if (!access (g_file, F_OK)) stop ("%s exists", g_file);
  /* 1. The file: pinned before it has blocks (f2fs refuses to pin a file with blocks), then fully allocated in whole sections. */
  char dir[PATH_MAX];
  snprintf (dir, sizeof dir, "%s", g_file);
  char *slash = strrchr (dir, '/');
  if (!slash) stop ("bad --file");
  *slash = 0;
  if (mkdir (dir, 0700) && errno != EEXIST) stop ("mkdir %s", dir);
  int fd = open (g_file, O_CREAT | O_EXCL | O_RDWR | O_CLOEXEC, 0600);
  if (fd < 0) stop ("create %s", g_file);
  uint32_t one = 1, gc = 0;
  if (ioctl (fd, F2FS_IOC_SET_PIN_FILE, &one) < 0) stop ("F2FS_IOC_SET_PIN_FILE");
  say ("allocating %" PRIu64 " GiB (pinned)...\n", sizeGiB);
  for (off_t previous = -1;;) {   /* f2fs may stop early with EAGAIN; go on while it makes progress (as libfiemap does) */
    if (!fallocate (fd, 0, 0, (off_t) size)) break;
    struct stat now;
    if (errno != EAGAIN || fstat (fd, &now) || now.st_size == previous) stop ("fallocate");
    previous = now.st_size;
  }
  if (fsync (fd)) stop ("fsync");
  errno = 0;
  if (!is_pinned (fd, &gc) || gc) stop ("the file is not pinned after allocation (GC failures %u)", gc);
  uint32_t n = 0;
  df_extent *ext = file_extents (fd, size, &e, &n);
  const uint64_t segments = size / SEG_BYTES;
  uint64_t *seg = segment_blocks (ext, n, segments);
  say ("allocated: %u extents, %" PRIu64 " segments\n", n, segments);
  /* 2. Mapping proof: block 0 of every segment, raw before; written through f2fs (in place: the file is pinned); raw after. */
  int raw = open (g_userdata, O_RDONLY | O_DIRECT | O_CLOEXEC);
  int dfd = open (g_file, O_RDWR | O_DIRECT | O_CLOEXEC);
  if (raw < 0 || dfd < 0) stop ("open for the mapping proof");
  uint64_t *before = malloc (segments * 8);
  uint8_t *b = block_alloc (DF_BLOCK), *pat = block_alloc (DF_BLOCK);
  uint8_t key[32];
  if (!before) stop ("out of memory");
  random_bytes (key, sizeof key);
  for (uint64_t i = 0; i < segments; ++i) {
    if (pread (raw, b, DF_BLOCK, (off_t) (seg[i] * DF_BLOCK)) != DF_BLOCK) stop ("raw read (proof, before)");
    before[i] = fnv64 (b, DF_BLOCK);
  }
  int buffered = 0;
  for (uint64_t i = 0; i < segments; ++i) {
    uint64_t x = fnv64 (key, sizeof key) ^ (i * 0x9E3779B97F4A7C15ULL) ^ 1;   /* xorshift64*: a pattern that differs per segment */
    for (unsigned k = 0; k < DF_BLOCK; k += 8) {
      x ^= x >> 12; x ^= x << 25; x ^= x >> 27;
      uint64_t v = x * 0x2545F4914F6CDD1DULL;
      memcpy (pat + k, &v, 8);
    }
    ssize_t w = pwrite (dfd, pat, DF_BLOCK, (off_t) (i * SEG_BYTES));
    if (w < 0 && errno == EINVAL && !buffered) {   /* no direct I/O for this file: buffered writes, synced and dropped below */
      close (dfd);
      dfd = open (g_file, O_RDWR | O_CLOEXEC);
      if (dfd < 0) stop ("reopen %s", g_file);
      buffered = 1;
      w = pwrite (dfd, pat, DF_BLOCK, (off_t) (i * SEG_BYTES));
    }
    if (w != DF_BLOCK) stop ("write through f2fs (proof)");
  }
  if (fdatasync (dfd)) stop ("fdatasync (proof)");
  posix_fadvise (dfd, 0, 0, POSIX_FADV_DONTNEED);
  uint64_t unchanged = 0;
  for (uint64_t i = 0; i < segments; ++i) {
    if (pread (raw, b, DF_BLOCK, (off_t) (seg[i] * DF_BLOCK)) != DF_BLOCK) stop ("raw read (proof, after)");
    if (fnv64 (b, DF_BLOCK) == before[i]) ++unchanged;
  }
  close (raw);
  close (dfd);
  errno = 0;
  if (unchanged) stop ("%" PRIu64 " of %" PRIu64 " segments did not change on the raw device: the mapping is not proven; nothing raw was written, "
                       "delete %s", unchanged, segments, g_file);
  uint32_t n2 = 0;
  df_extent *again = file_extents (fd, size, &e, &n2);
  if (!same_extents (ext, n, again, n2) || !is_pinned (fd, &gc) || gc) stop ("the file moved during the proof");
  free (again);
  say ("mapping proven for all %" PRIu64 " segments\n", segments);
  /* 3. The descriptor (not yet written) and the canaries, raw, into the proven blocks only. */
  df_header h;
  memset (&h, 0, sizeof h);
  h.state = DF_STATE_ACTIVE;
  h.generation = 1;
  for (int i = 0; i < 2; ++i) if (s.rc[i] == DF_OK && s.h[i].generation >= h.generation) h.generation = s.h[i].generation + 1;
  random_bytes (h.file_id, sizeof h.file_id);
  random_bytes (h.nonce, sizeof h.nonce);
  h.part_first = e.part_first;
  h.part_last = e.part_last;
  h.fs_main = e.fs_main;
  h.fs_blocks = e.fs_blocks;
  struct stat st;
  if (fstat (fd, &st)) stop ("fstat");
  h.file_ino = (uint64_t) st.st_ino;
  h.created = (uint64_t) time (NULL);
  uint8_t *slot = block_alloc ((size_t) DF_SLOT_BLOCKS * DF_BLOCK);
  uint32_t slotBlocks = df_build_slot (&h, ext, n, slot, df_sha256);
  errno = 0;
  if (!slotBlocks) stop ("descriptor build");
  raw = open (g_userdata, O_RDWR | O_DIRECT | O_CLOEXEC);
  if (raw < 0) stop ("open %s for the canaries", g_userdata);
  for (uint64_t i = 0; i < segments; ++i) {
    df_canary_block (&h, i, seg[i], b, df_sha256);
    if (pwrite (raw, b, DF_BLOCK, (off_t) (seg[i] * DF_BLOCK)) != DF_BLOCK) stop ("canary write, segment %" PRIu64, i);
  }
  if (fsync (raw)) stop ("flush the canaries");
  close (raw);
  uint64_t firstBad = 0;
  errno = 0;
  if (canaries_bad (&h, seg, segments, &firstBad)) stop ("canary read-back failed at segment %" PRIu64, firstBad);
  again = file_extents (fd, size, &e, &n2);
  if (!same_extents (ext, n, again, n2) || !is_pinned (fd, &gc) || gc) stop ("the file moved while the canaries were written");
  free (again);
  /* 4. Immutable: no unlink, rename, truncate or write through f2fs from now on (remove clears it). */
  set_immutable (fd, 1);
  close (fd);
  /* 5. Publish: the slot that does not hold the newest valid descriptor. */
  int newest = slots_newest (&s), target = newest == 0 ? 1 : 0;
  slot_write (target, slot, slotBlocks);
  slots_t s2;
  slots_read (&s2);
  errno = 0;
  if (slots_newest (&s2) != target || s2.h[target].generation != h.generation) stop ("the new descriptor is not the one in force");
  printf ("CREATED %s: %" PRIu64 " GiB, %u extents, %" PRIu64 " segments, %.2f GiB of Data blocks, descriptor generation %" PRIu64 " in slot %c\n",
          g_file, sizeGiB, n, segments, h.data_blocks * 4096.0 / 1073741824.0, h.generation, 'A' + target);
  print_disk_limit (&h);
  return 0;
}

static int cmd_publish (const char *confirm)
{
  require_confirm (confirm, "publish");
  env_t e;
  slots_t s;
  df_header h;
  df_extent *ext = NULL;
  discover (&e);
  slots_read (&s);
  print_slots (&s);
  live_descriptor (&e, &s, 1, &h, &ext);
  errno = 0;
  if (check_file (&e, &h, ext, 1)) stop ("the file is not intact: not published");
  h.state = DF_STATE_ACTIVE;
  h.generation = s.h[slots_newest (&s)].generation + 1;
  uint8_t *slot = block_alloc ((size_t) DF_SLOT_BLOCKS * DF_BLOCK);
  uint32_t blocks = df_build_slot (&h, ext, h.extent_count, slot, df_sha256);
  int target = slots_newest (&s) == 0 ? 1 : 0;
  slot_write (target, slot, blocks);
  printf ("PUBLISHED generation %" PRIu64 " in slot %c\n", h.generation, 'A' + target);
  return 0;
}

static int cmd_revoke (const char *confirm)
{
  require_confirm (confirm, "revoke");
  env_t e;
  slots_t s;
  discover (&e);
  slots_read (&s);
  revoke_newest (&s);
  return 0;
}

/* For a boot service: nothing to do without a live descriptor; otherwise verify, and revoke when anything fails. */
static int cmd_guard (void)
{
  env_t e;
  slots_t s;
  df_header h;
  df_extent *ext = NULL;
  g_quiet = 1;
  discover (&e);
  slots_read (&s);
  const int newest = slots_newest (&s);
  if (newest < 0 || s.h[newest].state == DF_STATE_REVOKED) { printf ("guard: no live descriptor\n"); return 0; }
  h = s.h[newest];
  int bad = df_check_partition (&h, e.part_first, e.part_last) || h.fs_main != e.fs_main || h.fs_blocks != e.fs_blocks;
  if (!bad) {
    live_descriptor (&e, &s, 0, &h, &ext);
    bad = check_file (&e, &h, ext, 1);
  }
  if (!bad) { printf ("guard: ok (generation %" PRIu64 ")\n", h.generation); return 0; }
  g_quiet = 0;
  printf ("guard: the file is not intact\n");
  revoke_newest (&s);
  return 1;
}

static int cmd_selftest_check (uint32_t k)
{
  env_t e;
  slots_t s;
  df_header h;
  df_extent *ext = NULL;
  discover (&e);
  slots_read (&s);
  live_descriptor (&e, &s, 1, &h, &ext);
  errno = 0;
  if (!k || k > 32 || k > h.data_blocks) stop ("--blocks 1..32");
  int raw = open (g_userdata, O_RDONLY | O_DIRECT | O_CLOEXEC);
  if (raw < 0) stop ("open %s", g_userdata);
  uint8_t *got = block_alloc (DF_BLOCK), *want = block_alloc (DF_BLOCK);
  unsigned good = 0;
  for (uint64_t w = h.data_blocks - k; w < h.data_blocks; ++w) {
    const uint64_t rel = df_data_rel (ext, h.extent_count, w);
    if (pread (raw, got, DF_BLOCK, (off_t) (rel * DF_BLOCK)) != DF_BLOCK) stop ("raw read");
    df_selftest_block (&h, w, rel, want, df_sha256);
    good += !memcmp (got, want, DF_BLOCK);
  }
  close (raw);
  printf ("selftest-check: %u of %u blocks hold XNU's pattern%s\n", good, k, good == k ? " (XNU wrote exactly there)" : "");
  return good == k ? 0 : 1;
}

static int cmd_remove (const char *confirm)
{
  require_confirm (confirm, "remove");
  env_t e;
  slots_t s;
  discover (&e);
  slots_read (&s);
  revoke_newest (&s);
  int fd = open (g_file, O_RDONLY | O_CLOEXEC);
  if (fd >= 0) {
    if (file_flags (fd) & FS_IMMUTABLE_FL) set_immutable (fd, 0);
    close (fd);
  }
  errno = 0;
  if (unlink (g_file)) stop ("unlink %s", g_file);
  printf ("REMOVED %s (its blocks go back to f2fs; XNU refuses the revoked descriptor)\n", g_file);
  return 0;
}

static int cmd_probe (void)
{
  env_t e;
  slots_t s;
  discover (&e);
  print_env (&e);
  say ("pin_file feature: %s; unixnu layout: %s\n", access ("/sys/fs/f2fs/features/pin_file", F_OK) ? "missing" : "present",
       unixnu_layout_ok (1) ? "root manifest, windows past 1 MiB" : "NOT as expected");
  slots_read (&s);
  print_slots (&s);
  int fd = open (g_file, O_RDONLY | O_CLOEXEC);
  if (fd >= 0) {
    struct stat st;
    uint32_t gc = 0;
    if (!fstat (fd, &st)) say ("file: %s, %lld bytes, inode %llu, pinned %d (GC failures %u), immutable %d\n", g_file, (long long) st.st_size,
                               (unsigned long long) st.st_ino, is_pinned (fd, &gc), gc, !!(file_flags (fd) & FS_IMMUTABLE_FL));
    close (fd);
  } else {
    say ("file: %s absent\n", g_file);
  }
  return 0;
}

int main (int argc, char **argv)
{
  const char *cmd = argc > 1 ? argv[1] : "", *confirm = NULL;
  uint64_t sizeGiB = 0, minFree = 48, blocks = 8;
  for (int i = 2; i < argc; ++i) {
    if (!strcmp (argv[i], "--quiet")) g_quiet = 1;
    else if (!strcmp (argv[i], "--confirm") && i + 1 < argc) confirm = argv[++i];
    else if (!strcmp (argv[i], "--size-gib") && i + 1 < argc) sizeGiB = strtoull (argv[++i], NULL, 10);
    else if (!strcmp (argv[i], "--min-free-gib") && i + 1 < argc) minFree = strtoull (argv[++i], NULL, 10);
    else if (!strcmp (argv[i], "--blocks") && i + 1 < argc) blocks = strtoull (argv[++i], NULL, 10);
    else if (!strcmp (argv[i], "--file") && i + 1 < argc) g_file = argv[++i];
    else { fprintf (stderr, "unknown argument %s\n", argv[i]); return 64; }
  }
  if (geteuid ()) { fprintf (stderr, "run as root\n"); return 64; }
  if (!strcmp (cmd, "probe")) return cmd_probe ();
  if (!strcmp (cmd, "verify")) return cmd_verify ();
  if (!strcmp (cmd, "create")) return cmd_create (sizeGiB, confirm, minFree);
  if (!strcmp (cmd, "publish")) return cmd_publish (confirm);
  if (!strcmp (cmd, "revoke")) return cmd_revoke (confirm);
  if (!strcmp (cmd, "guard")) return cmd_guard ();
  if (!strcmp (cmd, "selftest-check")) return cmd_selftest_check ((uint32_t) blocks);
  if (!strcmp (cmd, "remove")) return cmd_remove (confirm);
  fprintf (stderr, "usage: unixnu-datafile probe | verify | create --size-gib N --confirm SERIAL:N | publish --confirm SERIAL:publish |\n"
                   "       revoke --confirm SERIAL:revoke | guard | selftest-check [--blocks K] | remove --confirm SERIAL:remove [--file PATH]\n");
  return 64;
}
