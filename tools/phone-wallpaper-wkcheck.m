// iOS simulator probe of test-phone-wallpaper-overlay.py (WALLPAPER_SIM=1): asks the simulator runtime's own WallpaperKit whether
// +[WKWallpaperBundle shouldLoadWallpaperBundleAtURL:] keeps each wallpaper bundle named on the command line, and prints the
// _WKWallpaperRepresentingLogicalScreenClassAny constant and WKLogicalScreenClassString() it compares logicalScreenClass with.
// Paths outside the simulator's data and temporary directories get the full check (those two skip it).
//   xcrun --sdk iphonesimulator clang -arch arm64 -mios-simulator-version-min=17.0 -fobjc-arc -framework Foundation ... -o wkcheck
//   xcrun simctl spawn <device> wkcheck BUNDLE.wallpaper ...   ->   "LOADS  path" / "REJECT path"
#import <Foundation/Foundation.h>
#import <dlfcn.h>

int main(int argc, char **argv) {
    @autoreleasepool {
        void *kit = dlopen("/System/Library/PrivateFrameworks/WallpaperKit.framework/WallpaperKit", RTLD_NOW);
        if (!kit) {
            printf("dlopen failed: %s\n", dlerror());
            return 2;
        }
        NSString *__unsafe_unretained *any = (NSString *__unsafe_unretained *)dlsym(kit, "WKWallpaperRepresentingLogicalScreenClassAny");
        NSString *(*screen)(void) = (NSString * (*)(void)) dlsym(kit, "WKLogicalScreenClassString");
        printf("any constant: '%s'\n", any ? (*any).UTF8String : "?");
        printf("this device: '%s'\n", screen ? screen().UTF8String : "?");
        Class bundle = NSClassFromString(@"WKWallpaperBundle");
        SEL selector = NSSelectorFromString(@"shouldLoadWallpaperBundleAtURL:");
        if (!bundle || ![bundle respondsToSelector:selector]) {
            printf("no +[WKWallpaperBundle shouldLoadWallpaperBundleAtURL:]\n");
            return 2;
        }
        BOOL (*shouldLoad)(id, SEL, NSURL *) = (BOOL (*)(id, SEL, NSURL *))[bundle methodForSelector:selector];
        for (int i = 1; i < argc; i++)
            printf("%s %s\n", shouldLoad(bundle, selector, [NSURL fileURLWithPath:@(argv[i])]) ? "LOADS " : "REJECT", argv[i]);
    }
    return 0;
}
