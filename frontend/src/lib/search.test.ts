import { describe, expect, it } from "vitest";
import {
  formFromParams,
  isSearching,
  paramsFromForm,
  priceProblem,
  queryFromForm,
  withoutRepeats,
} from "./search";

const from = (text: string) => formFromParams(new URLSearchParams(text));

describe("formFromParams", () => {
  it("reads every field", () => {
    expect(
      from("q=jazz&category=CONCERT&city=Kochi&ticket=VIP&min=500&max=2,000&sort=-price"),
    ).toEqual({
      q: "jazz",
      category: "CONCERT",
      city: "Kochi",
      ticket: "VIP",
      min: "500",
      max: "2,000",
      sort: "-price",
    });
  });

  it("drops what the API would refuse, and what is not a search field", () => {
    expect(from("q=++&category=OPERA&sort=seller&min=cheap&max=-5&cursor=abc")).toEqual({});
  });

  it("does not trust an inherited property name as a sort", () => {
    expect(from("sort=toString")).toEqual({});
  });

  it("drops prices the API would refuse, so a bad address still searches", () => {
    expect(from("min=600&max=500&city=Kochi")).toEqual({ city: "Kochi" });
    expect(from("max=200000")).toEqual({});
    expect(from("min=0")).toEqual({});
  });

  it("treats the default sort as no sort, so one search has one cache entry", () => {
    expect(from("sort=price")).toEqual({});
  });
});

describe("paramsFromForm", () => {
  it("leaves out empty fields", () => {
    expect(paramsFromForm({ q: " jazz ", city: "", min: "  " })).toEqual({ q: "jazz" });
  });

  it("leaves the default sort out: an untouched form has a clean address", () => {
    expect(paramsFromForm({ q: "", sort: "price" })).toEqual({});
    expect(paramsFromForm({ sort: "-price" })).toEqual({ sort: "-price" });
  });

  it("leaves out a lowest price of 0, which asks for nothing", () => {
    expect(paramsFromForm({ min: "0" })).toEqual({});
    expect(paramsFromForm({ min: "0.00", max: "500" })).toEqual({ max: "500" });
  });
});

describe("queryFromForm", () => {
  it("sends prices in paise", () => {
    expect(queryFromForm({ min: "1,500", max: "1999.50", ticket: "VIP" })).toEqual({
      min_price_paise: 150000,
      max_price_paise: 199950,
      ticket_category: "VIP",
    });
  });

  it("asks for nothing when nothing was filled in", () => {
    expect(queryFromForm({})).toEqual({});
  });
});

describe("priceProblem", () => {
  it("accepts a range, one end, or neither", () => {
    expect(priceProblem({})).toEqual({});
    expect(priceProblem({ min: "500" })).toEqual({});
    expect(priceProblem({ min: "500", max: "500" })).toEqual({});
    expect(priceProblem({ min: "0" })).toEqual({});
  });

  it("names what to fix, on the field to fix", () => {
    expect(priceProblem({ min: "cheap" }).min).toMatch(/rupees/);
    expect(priceProblem({ min: "600", max: "500" }).min).toMatch(/above/);
    expect(priceProblem({ max: "9,99,99,999" })).toEqual({
      max: expect.stringMatching(/costs more/) as string,
    });
    expect(priceProblem({ max: "0" }).max).toMatch(/rupees/);
  });
});

describe("isSearching", () => {
  it("is about filters, not the order", () => {
    expect(isSearching({})).toBe(false);
    expect(isSearching({ sort: "-price" })).toBe(false);
    expect(isSearching({ city: "Kochi" })).toBe(true);
  });
});

describe("withoutRepeats", () => {
  it("shows a listing that moved between two pages once, as it is now", () => {
    const items = [
      { id: "a", price: 2 },
      { id: "b", price: 1 },
      { id: "a", price: 0 },
    ];
    expect(withoutRepeats(items)).toEqual([
      { id: "a", price: 0 },
      { id: "b", price: 1 },
    ]);
  });
});
