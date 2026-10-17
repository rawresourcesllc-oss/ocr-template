#import <Foundation/Foundation.h>
#import <ImageIO/ImageIO.h>
#import <Vision/Vision.h>
#import <CoreGraphics/CoreGraphics.h>
#import <CoreText/CoreText.h>
#import <sqlite3.h>
#import <stdatomic.h>

#define CONCURRENT_WORKERS 8

static sqlite3 *db = NULL;
static dispatch_queue_t dbQueue;
static NSMutableSet *processedPathsSet = nil;
static NSLock *setLock = nil;

static atomic_size_t g_processedCount = 0;
static atomic_size_t g_pageCount = 0;
static atomic_size_t g_skippedCount = 0;
static NSDate *g_startTime = nil;

void init_database(NSString *dbPath) {
    if (sqlite3_open([dbPath UTF8String], &db) != SQLITE_OK) {
        NSLog(@"Error opening database: %s", sqlite3_errmsg(db));
        exit(1);
    }
    
    // Enable WAL mode and tuned cache for multi-threaded performance
    sqlite3_exec(db, "PRAGMA journal_mode=WAL;", NULL, NULL, NULL);
    sqlite3_exec(db, "PRAGMA synchronous=NORMAL;", NULL, NULL, NULL);
    sqlite3_exec(db, "PRAGMA cache_size=-64000;", NULL, NULL, NULL); // 64MB cache
    
    const char *create_sql = 
        "CREATE TABLE IF NOT EXISTS instruments ("
        "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  county TEXT,"
        "  relative_path TEXT UNIQUE,"
        "  file_name TEXT,"
        "  page_count INTEGER,"
        "  pdf_path TEXT,"
        "  txt_path TEXT,"
        "  json_path TEXT,"
        "  full_text TEXT,"
        "  processed_at TEXT"
        ");"
        "CREATE VIRTUAL TABLE IF NOT EXISTS instruments_fts USING fts5("
        "  county,"
        "  file_name,"
        "  full_text,"
        "  content='instruments',"
        "  content_rowid='id'"
        ");"
        "CREATE TRIGGER IF NOT EXISTS instruments_ai AFTER INSERT ON instruments BEGIN "
        "  INSERT INTO instruments_fts(rowid, county, file_name, full_text) "
        "  VALUES (new.id, new.county, new.file_name, new.full_text); "
        "END;";
        
    char *errMsg = NULL;
    if (sqlite3_exec(db, create_sql, NULL, NULL, &errMsg) != SQLITE_OK) {
        NSLog(@"Database setup error: %s", errMsg);
        sqlite3_free(errMsg);
    }
    
    // Pre-load all already processed relative paths into an in-memory hash set
    processedPathsSet = [NSMutableSet set];
    setLock = [[NSLock alloc] init];
    
    const char *query_sql = "SELECT relative_path FROM instruments;";
    sqlite3_stmt *stmt;
    if (sqlite3_prepare_v2(db, query_sql, -1, &stmt, NULL) == SQLITE_OK) {
        while (sqlite3_step(stmt) == SQLITE_ROW) {
            const unsigned char *text = sqlite3_column_text(stmt, 0);
            if (text) {
                [processedPathsSet addObject:[NSString stringWithUTF8String:(const char *)text]];
            }
        }
        sqlite3_finalize(stmt);
    }
    
    NSLog(@"Loaded %lu existing processed records into in-memory checkpoint index.", (unsigned long)processedPathsSet.count);
}

BOOL is_already_processed(NSString *relPath) {
    [setLock lock];
    BOOL exists = [processedPathsSet containsObject:relPath];
    [setLock unlock];
    return exists;
}

void save_instrument_record_sync(NSString *county, NSString *relPath, NSString *fileName, int pageCount, 
                                 NSString *pdfPath, NSString *txtPath, NSString *jsonPath, NSString *fullText) {
    const char *sql = "INSERT OR REPLACE INTO instruments "
                      "(county, relative_path, file_name, page_count, pdf_path, txt_path, json_path, full_text, processed_at) "
                      "VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'));";
    sqlite3_stmt *stmt;
    if (sqlite3_prepare_v2(db, sql, -1, &stmt, NULL) == SQLITE_OK) {
        sqlite3_bind_text(stmt, 1, [county UTF8String], -1, SQLITE_STATIC);
        sqlite3_bind_text(stmt, 2, [relPath UTF8String], -1, SQLITE_STATIC);
        sqlite3_bind_text(stmt, 3, [fileName UTF8String], -1, SQLITE_STATIC);
        sqlite3_bind_int(stmt, 4, pageCount);
        sqlite3_bind_text(stmt, 5, [pdfPath UTF8String], -1, SQLITE_STATIC);
        sqlite3_bind_text(stmt, 6, [txtPath UTF8String], -1, SQLITE_STATIC);
        sqlite3_bind_text(stmt, 7, [jsonPath UTF8String], -1, SQLITE_STATIC);
        sqlite3_bind_text(stmt, 8, [fullText UTF8String], -1, SQLITE_STATIC);
        
        if (sqlite3_step(stmt) != SQLITE_DONE) {
            NSLog(@"Failed to insert record: %s", sqlite3_errmsg(db));
        }
        sqlite3_finalize(stmt);
    }
    
    [setLock lock];
    [processedPathsSet addObject:relPath];
    [setLock unlock];
}

BOOL process_image_file(NSString *filePath, NSString *relPath, NSString *county, NSString *outBaseDir) {
    NSURL *fileUrl = [NSURL fileURLWithPath:filePath];
    CGImageSourceRef src = CGImageSourceCreateWithURL((__bridge CFURLRef)fileUrl, NULL);
    if (!src) return NO;
    
    size_t pageCount = CGImageSourceGetCount(src);
    if (pageCount == 0) {
        CFRelease(src);
        return NO;
    }
    
    NSString *fileName = [filePath lastPathComponent];
    NSString *baseName = [fileName stringByDeletingPathExtension];
    NSString *relDir = [relPath stringByDeletingLastPathComponent];
    
    NSString *pdfDir = [outBaseDir stringByAppendingPathComponent:[NSString stringWithFormat:@"Searchable_PDFs/%@", relDir]];
    NSString *txtDir = [outBaseDir stringByAppendingPathComponent:[NSString stringWithFormat:@"Text_JSON/%@", relDir]];
    
    [[NSFileManager defaultManager] createDirectoryAtPath:pdfDir withIntermediateDirectories:YES attributes:nil error:nil];
    [[NSFileManager defaultManager] createDirectoryAtPath:txtDir withIntermediateDirectories:YES attributes:nil error:nil];
    
    NSString *pdfPath = [pdfDir stringByAppendingPathComponent:[NSString stringWithFormat:@"%@.pdf", baseName]];
    NSString *txtPath = [txtDir stringByAppendingPathComponent:[NSString stringWithFormat:@"%@.txt", baseName]];
    NSString *jsonPath = [txtDir stringByAppendingPathComponent:[NSString stringWithFormat:@"%@.json", baseName]];
    
    NSURL *pdfUrl = [NSURL fileURLWithPath:pdfPath];
    CGContextRef pdfCtx = CGPDFContextCreateWithURL((__bridge CFURLRef)pdfUrl, NULL, NULL);
    if (!pdfCtx) {
        CFRelease(src);
        return NO;
    }
    
    NSMutableArray *pagesJson = [NSMutableArray array];
    NSMutableString *fullText = [NSMutableString string];
    
    for (size_t i = 0; i < pageCount; i++) {
        @autoreleasepool {
            CGImageRef img = CGImageSourceCreateImageAtIndex(src, i, NULL);
            if (!img) continue;
            
            size_t width = CGImageGetWidth(img);
            size_t height = CGImageGetHeight(img);
            CGRect pageRect = CGRectMake(0, 0, width, height);
            
            CGPDFContextBeginPage(pdfCtx, (__bridge CFDictionaryRef)@{
                (__bridge NSString *)kCGPDFContextMediaBox: [NSValue valueWithRect:pageRect]
            });
            
            // Draw original scan image
            CGContextDrawImage(pdfCtx, pageRect, img);
            
            // Apple Vision OCR - Accurate Deep Neural Mode
            VNImageRequestHandler *handler = [[VNImageRequestHandler alloc] initWithCGImage:img options:@{}];
            VNRecognizeTextRequest *req = [[VNRecognizeTextRequest alloc] init];
            req.recognitionLevel = VNRequestTextRecognitionLevelAccurate;
            req.usesLanguageCorrection = YES;
            req.recognitionLanguages = @[@"en-US"];
            req.minimumTextHeight = 0.0;
            req.revision = VNRecognizeTextRequestRevision3;
            
            NSError *err = nil;
            [handler performRequests:@[req] error:&err];
            
            NSMutableString *pageText = [NSMutableString string];
            NSMutableArray *linesJson = [NSMutableArray array];
            
            if (!err && req.results.count > 0) {
                CGContextSetTextDrawingMode(pdfCtx, kCGTextInvisible);
                
                for (VNRecognizedTextObservation *obs in req.results) {
                    VNRecognizedText *topCandidate = [[obs topCandidates:1] firstObject];
                    if (!topCandidate) continue;
                    
                    NSString *str = topCandidate.string;
                    [pageText appendFormat:@"%@\n", str];
                    
                    CGRect normBox = obs.boundingBox;
                    CGRect textRect = CGRectMake(
                        normBox.origin.x * width,
                        normBox.origin.y * height,
                        normBox.size.width * width,
                        normBox.size.height * height
                    );
                    
                    [linesJson addObject:@{
                        @"text": str,
                        @"confidence": @(topCandidate.confidence),
                        @"box": @{
                            @"x": @(textRect.origin.x),
                            @"y": @(textRect.origin.y),
                            @"w": @(textRect.size.width),
                            @"h": @(textRect.size.height)
                        }
                    }];
                    
                    CTFontRef font = CTFontCreateWithName(CFSTR("Helvetica"), MAX(10.0, textRect.size.height * 0.8), NULL);
                    if (font) {
                        NSDictionary *attrs = @{ (__bridge NSString *)kCTFontAttributeName: (__bridge id)font };
                        NSAttributedString *attrStr = [[NSAttributedString alloc] initWithString:str attributes:attrs];
                        CTLineRef line = CTLineCreateWithAttributedString((__bridge CFAttributedStringRef)attrStr);
                        if (line) {
                            CGContextSetTextPosition(pdfCtx, textRect.origin.x, textRect.origin.y);
                            CTLineDraw(line, pdfCtx);
                            CFRelease(line);
                        }
                        CFRelease(font);
                    }
                }
            }
            
            CGPDFContextEndPage(pdfCtx);
            CGImageRelease(img);
            
            [fullText appendFormat:@"--- PAGE %zu ---\n%@\n", i + 1, pageText];
            [pagesJson addObject:@{
                @"page_index": @(i + 1),
                @"width": @(width),
                @"height": @(height),
                @"lines": linesJson,
                @"raw_text": pageText
            }];
        }
    }
    
    CGPDFContextClose(pdfCtx);
    CGContextRelease(pdfCtx);
    CFRelease(src);
    
    // Format 1a: Plain UTF-8 Text
    [fullText writeToFile:txtPath atomically:YES encoding:NSUTF8StringEncoding error:nil];
    
    // Format 1b: Structured JSON
    NSDictionary *docJson = @{
        @"county": county,
        @"file_name": fileName,
        @"relative_path": relPath,
        @"total_pages": @(pageCount),
        @"pages": pagesJson
    };
    NSData *jsonData = [NSJSONSerialization dataWithJSONObject:docJson options:NSJSONWritingPrettyPrinted error:nil];
    [jsonData writeToFile:jsonPath atomically:YES];
    
    // Format 2: Save to SQLite Database (via dedicated serial writer queue)
    dispatch_async(dbQueue, ^{
        save_instrument_record_sync(county, relPath, fileName, (int)pageCount, pdfPath, txtPath, jsonPath, fullText);
    });
    
    atomic_fetch_add(&g_processedCount, 1);
    atomic_fetch_add(&g_pageCount, pageCount);
    
    size_t currentFiles = atomic_load(&g_processedCount);
    size_t currentPages = atomic_load(&g_pageCount);
    
    if (currentFiles % 25 == 0) {
        NSTimeInterval elapsed = [[NSDate date] timeIntervalSinceDate:g_startTime];
        double pagesPerSec = (elapsed > 0) ? (double)currentPages / elapsed : 0.0;
        double pagesPerMin = pagesPerSec * 60.0;
        NSLog(@"[Progress] %zu instruments (%zu pages) | Rate: %.1f pages/min | Latest: %@", 
              currentFiles, currentPages, pagesPerMin, fileName);
    }
    
    return YES;
}

int main(int argc, const char * argv[]) {
    @autoreleasepool {
        NSString *inDir = @"/Users/ryanwilson/Documents/County Clerks";
        NSString *outDir = @"/Users/ryanwilson/Documents/County Clerks_OCR";
        
        if (argc >= 2) inDir = [NSString stringWithUTF8String:argv[1]];
        if (argc >= 3) outDir = [NSString stringWithUTF8String:argv[2]];
        
        NSString *dbDir = [outDir stringByAppendingPathComponent:@"Database"];
        [[NSFileManager defaultManager] createDirectoryAtPath:dbDir withIntermediateDirectories:YES attributes:nil error:nil];
        NSString *dbPath = [dbDir stringByAppendingPathComponent:@"county_records.sqlite"];
        
        NSLog(@"=== Starting Optimized Multi-Threaded Apple Vision OCR Pipeline ===");
        NSLog(@"Workers: %d Concurrent Threads", CONCURRENT_WORKERS);
        NSLog(@"Accuracy: VNRequestTextRecognitionLevelAccurate (Deep Neural Model)");
        NSLog(@"Source: %@", inDir);
        NSLog(@"Destination: %@", outDir);
        NSLog(@"Database: %@", dbPath);
        
        dbQueue = dispatch_queue_create("com.ocr.db_writer", DISPATCH_QUEUE_SERIAL);
        init_database(dbPath);
        
        g_startTime = [NSDate date];
        dispatch_semaphore_t workerSemaphore = dispatch_semaphore_create(CONCURRENT_WORKERS);
        dispatch_queue_t workerQueue = dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0);
        dispatch_group_t taskGroup = dispatch_group_create();
        
        NSFileManager *fm = [NSFileManager defaultManager];
        NSDirectoryEnumerator *enumerator = [fm enumeratorAtPath:inDir];
        NSString *relPath;
        
        while ((relPath = [enumerator nextObject])) {
            NSString *ext = [[relPath pathExtension] lowercaseString];
            if ([ext isEqualToString:@"tif"] || [ext isEqualToString:@"tiff"] || [ext isEqualToString:@"pdf"] || [ext isEqualToString:@"jpg"]) {
                if (is_already_processed(relPath)) {
                    atomic_fetch_add(&g_skippedCount, 1);
                    continue;
                }
                
                NSString *fullPath = [inDir stringByAppendingPathComponent:relPath];
                NSArray *components = [relPath pathComponents];
                NSString *county = (components.count > 0) ? components[0] : @"Unknown";
                
                // Exclude Reeves County (transferred to another machine)
                if ([county isEqualToString:@"Reeves County, TX"] || [relPath containsString:@"Reeves County, TX"]) {
                    atomic_fetch_add(&g_skippedCount, 1);
                    continue;
                }
                
                // Wait for an available worker thread
                dispatch_semaphore_wait(workerSemaphore, DISPATCH_TIME_FOREVER);
                dispatch_group_enter(taskGroup);
                
                dispatch_async(workerQueue, ^{
                    @autoreleasepool {
                        process_image_file(fullPath, relPath, county, outDir);
                    }
                    dispatch_semaphore_signal(workerSemaphore);
                    dispatch_group_leave(taskGroup);
                });
            }
        }
        
        // Wait for all active worker threads to finish
        dispatch_group_wait(taskGroup, DISPATCH_TIME_FOREVER);
        
        // Ensure all database writes are committed
        dispatch_sync(dbQueue, ^{
            sqlite3_exec(db, "PRAGMA wal_checkpoint(FULL);", NULL, NULL, NULL);
        });
        
        NSTimeInterval totalElapsed = [[NSDate date] timeIntervalSinceDate:g_startTime];
        size_t finalFiles = atomic_load(&g_processedCount);
        size_t finalPages = atomic_load(&g_pageCount);
        size_t skipped = atomic_load(&g_skippedCount);
        
        NSLog(@"=== OCR Pipeline Finished in %.1f seconds ===", totalElapsed);
        NSLog(@"Total Processed: %zu instruments (%zu pages) | Skipped Existing: %zu", 
              finalFiles, finalPages, skipped);
              
        if (db) sqlite3_close(db);
    }
    return 0;
}
