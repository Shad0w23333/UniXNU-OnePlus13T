// Host helper of check-phone-wallpaper-overlay.py --render (macOS): loads a wallpaper's layered animation (.ca CAML bundle) with
// QuartzCore's own CAPackage reader (the class WallpaperKit's WKPlatformPackageView uses on the phone: +[CAPackage
// packageWithContentsOfURL:type:options:error:] with kCAPackageTypeCAMLBundle), lists every CGImage the layer tree and its LKStates
// hold, and renders the root layer with each named state applied, centred, into a WIDTH x HEIGHT (document units) black canvas at a
// quarter of the size. Prints one JSON object.
//
//   phone-wallpaper-render CA_DIR WIDTH HEIGHT [STATE ...]
//
// Build: xcrun clang -fobjc-arc -framework Foundation -framework QuartzCore -framework CoreGraphics phone-wallpaper-render.m -o ...
#import <CoreGraphics/CoreGraphics.h>
#import <Foundation/Foundation.h>
#import <QuartzCore/QuartzCore.h>

@interface CAPackage : NSObject
+ (id)packageWithContentsOfURL:(NSURL *)url type:(NSString *)type options:(NSDictionary *)options error:(NSError **)error;
- (CALayer *)rootLayer;
@end
extern NSString *const kCAPackageTypeCAMLBundle;

static BOOL isImage(id value) { return value && CFGetTypeID((__bridge CFTypeRef)value) == CGImageGetTypeID(); }

static NSDictionary *imageInfo(id value) {
    CGImageRef image = (__bridge CGImageRef)value;
    return @{@"width": @(CGImageGetWidth(image)), @"height": @(CGImageGetHeight(image)),
             @"bitsPerComponent": @(CGImageGetBitsPerComponent(image))};
}

static NSArray *statesOf(CALayer *layer) {
    @try {
        id states = [layer valueForKey:@"states"];
        return [states isKindOfClass:[NSArray class]] ? states : @[];
    } @catch (__unused id exception) {
        return @[];
    }
}

static void walk(CALayer *layer, NSMutableArray *images, NSMutableArray *states, NSUInteger *bare) {
    if (isImage(layer.contents)) {
        NSMutableDictionary *info = [imageInfo(layer.contents) mutableCopy];
        info[@"layer"] = layer.name ?: @"";
        [images addObject:info];
    } else {
        ++*bare;
    }
    for (id state in statesOf(layer)) {
        NSUInteger found = 0, missing = 0;
        NSMutableArray *sizes = [NSMutableArray array];
        for (id element in [state valueForKey:@"elements"]) {
            NSString *keyPath = nil;
            id value = nil;
            @try {
                keyPath = [element valueForKey:@"keyPath"];
                value = [element valueForKey:@"value"];
            } @catch (__unused id exception) {
                continue;
            }
            if (![keyPath isEqual:@"contents"])
                continue;
            if (isImage(value)) {
                ++found;
                [sizes addObject:imageInfo(value)];
            } else {
                ++missing;
            }
        }
        [states addObject:@{@"name": [state valueForKey:@"name"] ?: @"", @"images": @(found), @"missing": @(missing), @"sizes": sizes}];
    }
    for (CALayer *sublayer in layer.sublayers)
        walk(sublayer, images, states, bare);
}

static CALayer *load(NSURL *url, NSError **error) {
    return [[CAPackage packageWithContentsOfURL:url type:kCAPackageTypeCAMLBundle options:nil error:error] rootLayer];
}

static NSDictionary *render(NSURL *url, NSString *stateName, double width, double height) {
    NSError *error = nil;
    CALayer *root = load(url, &error);
    if (!root)
        return @{@"state": stateName, @"error": error.localizedDescription ?: @"no root layer"};
    BOOL applied = NO;
    NSMutableArray *pending = [NSMutableArray arrayWithObject:root];
    while (pending.count) {
        CALayer *layer = pending.lastObject;
        [pending removeLastObject];
        [pending addObjectsFromArray:layer.sublayers ?: @[]];
        for (id state in statesOf(layer)) {
            if (![[state valueForKey:@"name"] isEqual:stateName])
                continue;
            applied = YES;
            for (id element in [state valueForKey:@"elements"]) {
                CALayer *target = [element valueForKey:@"target"];
                NSString *keyPath = [element valueForKey:@"keyPath"];
                if (target && keyPath)
                    [target setValue:[element valueForKey:@"value"] forKeyPath:keyPath];
            }
        }
    }
    const double scale = 0.25;
    size_t w = (size_t)(width * scale), h = (size_t)(height * scale);
    CGColorSpaceRef space = CGColorSpaceCreateWithName(kCGColorSpaceSRGB);
    CGContextRef context = CGBitmapContextCreate(NULL, w, h, 8, w * 4, space, (CGBitmapInfo)kCGImageAlphaPremultipliedLast);
    CGColorSpaceRelease(space);
    CGContextSetRGBFillColor(context, 0, 0, 0, 1);
    CGContextFillRect(context, CGRectMake(0, 0, w, h));
    CGContextScaleCTM(context, scale, scale);
    CGContextTranslateCTM(context, width / 2 - root.bounds.size.width / 2, height / 2 - root.bounds.size.height / 2);
    [root renderInContext:context];
    const uint8_t *pixels = CGBitmapContextGetData(context);
    size_t dark = 0;
    double sum = 0;
    for (size_t i = 0; i < w * h; i++) {
        uint8_t r = pixels[i * 4], g = pixels[i * 4 + 1], b = pixels[i * 4 + 2];
        if (MAX(r, MAX(g, b)) < 16)
            ++dark;
        sum += (r + g + b) / 3.0;
    }
    CGContextRelease(context);
    return @{@"state": stateName, @"applied": @(applied), @"pixels": @(w * h), @"dark": @((double)dark / (w * h)),
             @"mean": @(sum / (w * h))};
}

int main(int argc, char **argv) {
    @autoreleasepool {
        if (argc < 4) {
            fprintf(stderr, "usage: phone-wallpaper-render CA_DIR WIDTH HEIGHT [STATE ...]\n");
            return 2;
        }
        NSURL *url = [NSURL fileURLWithPath:@(argv[1])];
        NSMutableDictionary *out = [NSMutableDictionary dictionary];
        NSError *error = nil;
        CALayer *root = load(url, &error);
        out[@"loaded"] = @(root != nil);
        if (!root) {
            out[@"error"] = error.localizedDescription ?: @"no root layer";
        } else {
            NSMutableArray *images = [NSMutableArray array], *states = [NSMutableArray array];
            NSUInteger bare = 0;
            walk(root, images, states, &bare);
            out[@"root"] = @[@(root.bounds.size.width), @(root.bounds.size.height)];
            out[@"layerImages"] = images;
            out[@"layersWithoutImage"] = @(bare);
            out[@"states"] = states;
            NSMutableArray *renders = [NSMutableArray array];
            for (int i = 4; i < argc; i++)
                [renders addObject:render(url, @(argv[i]), atof(argv[2]), atof(argv[3]))];
            out[@"renders"] = renders;
        }
        NSData *json = [NSJSONSerialization dataWithJSONObject:out options:NSJSONWritingSortedKeys error:nil];
        fwrite(json.bytes, 1, json.length, stdout);
        fputc('\n', stdout);
        return root ? 0 : 1;
    }
}
