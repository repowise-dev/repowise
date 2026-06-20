// Fixture for the C# performance dialect (io_in_loop / string_concat /
// blocking_sync_in_async). Every POSITIVE is a genuine per-iteration I/O
// boundary or a sync-over-async block; every NEGATIVE is a shape the dialect
// must NOT flag. Hand-counted expectations live in test_perf_csharp.py. NOTE:
// this file imports EF Core, so file-level db evidence is present; the
// in-memory-LINQ false-positive gating is tested inline instead.
using System.Collections.Generic;
using System.IO;
using System.Net.Http;
using System.Threading.Tasks;
using Microsoft.EntityFrameworkCore;

class PerfIoInLoop
{
    // --- POSITIVES ---------------------------------------------------------

    async Task EfAsyncN1(DbContext ctx, List<int> ids)
    {
        foreach (var id in ids)
        {
            await ctx.Users.ToListAsync();   // POSITIVE: EF *Async (unambiguous db)
        }
    }

    async Task HttpInLoop(HttpClient c, List<string> urls)
    {
        foreach (var u in urls)
        {
            await c.GetAsync(u);             // POSITIVE: network
        }
    }

    async Task SaveInLoop(DbContext ctx, List<int> ids)
    {
        foreach (var id in ids)
        {
            await ctx.SaveChangesAsync();    // POSITIVE: EF write
        }
    }

    void FileReadInLoop(List<string> paths)
    {
        foreach (var p in paths)
        {
            File.ReadAllText(p);             // POSITIVE: filesystem
        }
    }

    void SyncLinqInLoop(DbContext ctx, List<int> ids)
    {
        foreach (var id in ids)
        {
            ctx.Users.ToList();              // POSITIVE: sync LINQ, db import present
        }
    }

    string StringConcatInLoop(List<string> rows)
    {
        string s = "";
        foreach (var r in rows)
        {
            s += "line";                     // POSITIVE: quadratic string build
        }
        return s;
    }

    async Task SyncOverAsync(Task<int> task)
    {
        var r = task.Result;                 // POSITIVE: .Result blocks in async
        task.Wait();                         // POSITIVE: .Wait() blocks in async
    }

    // --- NEGATIVES ---------------------------------------------------------

    void PureCallInLoop(List<int> items)
    {
        int total = 0;
        foreach (var x in items)
        {
            total += x;                      // NEGATIVE: numeric +=, no I/O
        }
    }

    async Task AwaitedAsyncResult(Task<int> task)
    {
        var r = await task;                  // NEGATIVE: awaited, not .Result/.Wait
    }
}
