// kbwalk FILE NAMES-FILE [WIDTH]: decode every listed keyboard with Apple's own TUIKBGraphSerialization (TextInputUI of the
// simulator runtime) and one shared deserialization cache, the way TUIKeyboardLayoutFactory keeps it, walk each tree completely
// and check what the walk finds: subtrees are arrays, properties dictionaries, names strings; with WIDTH every keyboard is that
// wide and no shape reaches past it. Prints keyboardPrefixForWidth:andEdge: for WIDTH first. Built and run by
// check-in-simulator.sh; exit status 1 on any problem.
#import <Foundation/Foundation.h>
#import <CoreGraphics/CoreGraphics.h>
#import <objc/message.h>
#import <dlfcn.h>

static Class TreeClass, ShapeClass;
static long nTrees, nShapes, bad;
static double maxRight;
static NSMutableSet *visited;

static void checkShape(id s, NSString *where) {
    CGRect r = ((CGRect (*)(id, SEL))objc_msgSend)(s, NSSelectorFromString(@"frame"));
    CGRect p = ((CGRect (*)(id, SEL))objc_msgSend)(s, NSSelectorFromString(@"paddedFrame"));
    nShapes++;
    double right = MAX(CGRectGetMaxX(r), CGRectGetMaxX(p));
    if (right > maxRight) maxRight = right;
    if (r.origin.x < -0.01 || r.size.width < 0 || isnan(r.origin.x)) { bad++; printf("bad shape in %s\n", where.UTF8String); }
}

static void walk(id o, NSString *where, int depth) {
    if (!o || depth > 40) return;
    NSValue *key = [NSValue valueWithNonretainedObject:o];
    if ([visited containsObject:key]) return;
    [visited addObject:key];
    if ([o isKindOfClass:ShapeClass]) { checkShape(o, where); return; }
    if ([o isKindOfClass:[NSArray class]]) { for (id x in o) walk(x, where, depth + 1); return; }
    if ([o isKindOfClass:[NSDictionary class]]) { for (id k in o) { walk(k, where, depth + 1); walk(o[k], where, depth + 1); } return; }
    if ([o isKindOfClass:[NSSet class]] || [o isKindOfClass:[NSString class]] || [o isKindOfClass:[NSNumber class]]) return;
    if ([o isKindOfClass:TreeClass]) {
        nTrees++;
        id subs = ((id (*)(id, SEL))objc_msgSend)(o, NSSelectorFromString(@"subtrees"));
        id props = ((id (*)(id, SEL))objc_msgSend)(o, NSSelectorFromString(@"properties"));
        id cache = [o respondsToSelector:NSSelectorFromString(@"cache")] ? ((id (*)(id, SEL))objc_msgSend)(o, NSSelectorFromString(@"cache")) : nil;
        id name = ((id (*)(id, SEL))objc_msgSend)(o, NSSelectorFromString(@"name"));
        if (subs && ![subs isKindOfClass:[NSArray class]]) { bad++; printf("subtrees of %s in %s is %s\n", [name description].UTF8String, where.UTF8String, NSStringFromClass([subs class]).UTF8String); }
        if (props && ![props isKindOfClass:[NSDictionary class]]) { bad++; printf("properties of %s in %s is %s\n", [name description].UTF8String, where.UTF8String, NSStringFromClass([props class]).UTF8String); }
        if (name && ![name isKindOfClass:[NSString class]]) { bad++; printf("name in %s is %s\n", where.UTF8String, NSStringFromClass([name class]).UTF8String); }
        walk(subs, where, depth + 1); walk(props, where, depth + 1); walk(cache, where, depth + 1);
        return;
    }
    if ([NSStringFromClass([o class]) hasPrefix:@"UIKB"]) return;    // geometries, merge actions
    bad++; printf("unexpected %s in %s\n", NSStringFromClass([o class]).UTF8String, where.UTF8String);
}

int main(int argc, char **argv) {
    @autoreleasepool {
        dlopen("/System/Library/Frameworks/UIKit.framework/UIKit", RTLD_NOW);
        dlopen("/System/Library/PrivateFrameworks/TextInputUI.framework/TextInputUI", RTLD_NOW);
        TreeClass = NSClassFromString(@"UIKBTree"); ShapeClass = NSClassFromString(@"UIKBShape");
        Class S = NSClassFromString(@"TUIKBGraphSerialization");
        NSData *data = [NSData dataWithContentsOfFile:@(argv[1]) options:NSDataReadingMappedAlways error:nil];
        NSArray *names = [[NSString stringWithContentsOfFile:@(argv[2]) encoding:NSUTF8StringEncoding error:nil]
                          componentsSeparatedByCharactersInSet:[NSCharacterSet newlineCharacterSet]];
        id s = [[S alloc] init];
        ((void (*)(id, SEL, id))objc_msgSend)(s, NSSelectorFromString(@"setSerializedData:"), data);
        ((void (*)(id, SEL, id))objc_msgSend)(s, NSSelectorFromString(@"setDeserializationCache:"), [NSMutableDictionary dictionary]);
        long found = 0, missing = 0, widthBad = 0;
        double expect = argc > 3 ? atof(argv[3]) : 0;
        if (expect > 0) {
            NSString *p = ((id (*)(id, SEL, float, BOOL))objc_msgSend)(s, NSSelectorFromString(@"keyboardPrefixForWidth:andEdge:"),
                                                                     (float)round(expect), YES);
            printf("portrait prefix for %.4f (frinta %.0f): %s\n", expect, round(expect), p.UTF8String);
        }
        for (NSString *n in names) {
            if (!n.length) continue;
            visited = [NSMutableSet set];
            maxRight = 0;
            id kb = ((id (*)(id, SEL, id))objc_msgSend)(s, NSSelectorFromString(@"keyboardForName:"), n);
            if (!kb) { missing++; printf("missing %s\n", n.UTF8String); continue; }
            found++;
            walk(kb, n, 0);
            if (expect > 0) {
                id shape = ((id (*)(id, SEL))objc_msgSend)(kb, NSSelectorFromString(@"properties"))[@"KBshape"];
                CGRect r = ((CGRect (*)(id, SEL))objc_msgSend)(shape, NSSelectorFromString(@"frame"));
                if (fabs(r.size.width - expect) > 0.001 || maxRight > expect + 0.01) { widthBad++; printf("width %s: %.4f max right %.4f\n", n.UTF8String, r.size.width, maxRight); }
            }
        }
        printf("keyboards %ld missing %ld trees %ld shapes %ld problems %ld width-problems %ld\n", found, missing, nTrees, nShapes, bad, widthBad);
        return (missing || bad || widthBad) ? 1 : 0;
    }
}
